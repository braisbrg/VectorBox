import zipfile
import io
import pandas as pd
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from fastapi import UploadFile, HTTPException

from services.csv_parser import CSVParser

logger = logging.getLogger(__name__)

class DataProcessor:
    REQUIRED_FILES = ['ratings.csv', 'watchlist.csv', 'watched.csv']
    OPTIONAL_FILES = ['likes/films.csv', 'diary.csv'] # Sometimes just 'likes.csv' depending on export version

    @staticmethod
    async def process_zip_export(file: UploadFile) -> Tuple[List[Dict], List[str]]:
        """
        Process a Letterboxd export ZIP file.
        Returns a merged list of movie data and any errors.
        """
        if not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail="Invalid file format. Please upload a ZIP file.")

        content = await file.read()
        
        movies_map: Dict[str, Dict] = {} # Key: Title+Year, Value: Movie Data
        errors = []

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                file_list = z.namelist()
                
                # 1. Process Ratings (Primary Signal)
                if 'ratings.csv' in file_list:
                    with z.open('ratings.csv') as f:
                        ratings_df = pd.read_csv(f)
                        DataProcessor._process_ratings(ratings_df, movies_map)
                else:
                    errors.append("ratings.csv not found in ZIP")

                # 2. Process Watchlist (Plan to Watch)
                if 'watchlist.csv' in file_list:
                    with z.open('watchlist.csv') as f:
                        watchlist_df = pd.read_csv(f)
                        DataProcessor._process_watchlist(watchlist_df, movies_map)
                
                # 3. Process Likes (Implicit Positive)
                # Check for likes/films.csv or likes.csv
                likes_file = next((f for f in file_list if 'likes/films.csv' in f or f == 'likes.csv'), None)
                if likes_file:
                    with z.open(likes_file) as f:
                        likes_df = pd.read_csv(f)
                        DataProcessor._process_likes(likes_df, movies_map)

                # 4. Process Watched (History)
                if 'watched.csv' in file_list:
                    with z.open('watched.csv') as f:
                        watched_df = pd.read_csv(f)
                        DataProcessor._process_watched(watched_df, movies_map)

                # 5. Process Diary (Accurate Watch Dates)
                if 'diary.csv' in file_list:
                    with z.open('diary.csv') as f:
                        diary_df = pd.read_csv(f)
                        DataProcessor._process_diary(diary_df, movies_map)

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Corrupt or invalid ZIP file")
        except Exception as e:
            logger.error(f"Error processing ZIP: {e}")
            raise HTTPException(status_code=500, detail="Failed to process export file")

        return list(movies_map.values()), errors

    @staticmethod
    def _get_key(row) -> str:
        title = str(row.get('Name', '')).strip()
        year = str(row.get('Year', ''))
        return f"{title}_{year}"

    @staticmethod
    def _parse_date(date_str: Optional[str]):
        """Parse YYYY-MM-DD string to python date object"""
        if pd.isna(date_str) or not date_str:
            return None
        try:
            return datetime.strptime(str(date_str), '%Y-%m-%d').date()
        except ValueError:
            return None

    @staticmethod
    def _process_ratings(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue
            
            movies_map[key] = {
                "title": row['Name'],
                "year": int(row['Year']) if pd.notna(row['Year']) else None,
                "letterboxd_uri": row.get('Letterboxd URI'),
                "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                "watched_date": DataProcessor._parse_date(row.get('Date')),
                "review": None, # Ratings CSV doesn't always have reviews, reviews.csv does
                "is_watchlist": False,
                "is_liked": False,
                "is_watched": True
            }

    @staticmethod
    def _process_watchlist(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            if key not in movies_map:
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": row.get('Letterboxd URI'),
                    "rating": None,
                    "watched_date": None,
                    "review": None,
                    "is_watchlist": True,
                    "is_liked": False,
                    "is_watched": False
                }
            else:
                movies_map[key]["is_watchlist"] = True

    @staticmethod
    def _process_likes(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            if key in movies_map:
                movies_map[key]["is_liked"] = True
                # If unrated but liked, we can infer a high rating (e.g. 4.0) for clustering
                if movies_map[key]["rating"] is None:
                    movies_map[key]["implicit_rating"] = 4.0
            else:
                 movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": row.get('Letterboxd URI'),
                    "rating": None,
                    "implicit_rating": 4.0, # Liked but not rated
                    "watched_date": None,
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": True,
                    "is_watched": True # If liked, probably watched
                }

    @staticmethod
    def _process_watched(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            if key in movies_map:
                movies_map[key]["is_watched"] = True
                if not movies_map[key]["watched_date"]:
                    movies_map[key]["watched_date"] = DataProcessor._parse_date(row.get('Date'))
            else:
                # Just watched, no rating/like/watchlist
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": row.get('Letterboxd URI'),
                    "rating": None,
                    "watched_date": DataProcessor._parse_date(row.get('Date')),
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True
                }

    @staticmethod
    def _process_diary(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            # Diary has 'Watched Date' which is the actual date watched, vs 'Date' which is log date
            watched_date = DataProcessor._parse_date(row.get('Watched Date')) or DataProcessor._parse_date(row.get('Date'))

            if key in movies_map:
                movies_map[key]["is_watched"] = True
                # Prefer diary date over others
                if watched_date:
                    movies_map[key]["watched_date"] = watched_date
                # Also update rating if present in diary and not in ratings (rare but possible)
                if pd.notna(row.get('Rating')) and movies_map[key]["rating"] is None:
                    movies_map[key]["rating"] = float(row['Rating'])
            else:
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": row.get('Letterboxd URI'),
                    "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                    "watched_date": watched_date,
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True
                }

    @staticmethod
    def _process_reviews(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            review_text = row.get('Review')
            if pd.isna(review_text): continue

            if key in movies_map:
                movies_map[key]["review"] = str(review_text)
            else:
                # Review implies watched
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": row.get('Letterboxd URI'),
                    "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                    "watched_date": DataProcessor._parse_date(row.get('Watched Date')) or DataProcessor._parse_date(row.get('Date')),
                    "review": str(review_text),
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True
                }
