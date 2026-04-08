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

        CSV processing order matters — likes must be last so is_liked is never
        overwritten by a later ratings/diary pass.
        Order: ratings → watchlist → watched → diary → reviews → likes (always last)
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

                # 3. Process Watched (History)
                if 'watched.csv' in file_list:
                    with z.open('watched.csv') as f:
                        watched_df = pd.read_csv(f)
                        DataProcessor._process_watched(watched_df, movies_map)

                # 4. Process Diary (Accurate Watch Dates + rewatch count)
                if 'diary.csv' in file_list:
                    with z.open('diary.csv') as f:
                        diary_df = pd.read_csv(f)
                        DataProcessor._process_diary(diary_df, movies_map)

                # 5. Process Reviews (optional)
                if 'reviews.csv' in file_list:
                    with z.open('reviews.csv') as f:
                        reviews_df = pd.read_csv(f)
                        DataProcessor._process_reviews(reviews_df, movies_map)

                # 6. Process Likes — ALWAYS LAST so is_liked is never overwritten
                likes_file = next((f for f in file_list if 'likes/films.csv' in f or f == 'likes.csv'), None)
                if likes_file:
                    with z.open(likes_file) as f:
                        likes_df = pd.read_csv(f)
                        DataProcessor._process_likes(likes_df, movies_map)

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Corrupt or invalid ZIP file")
        except Exception as e:
            logger.error(f"Error processing ZIP: {e}")
            raise HTTPException(status_code=500, detail="Failed to process export file")

        return list(movies_map.values()), errors

    @staticmethod
    def _get_key(row) -> str:
        title = str(row.get('Name', '')).strip()
        raw_year = row.get('Year', '')
        try:
            year = str(int(float(raw_year))) if pd.notna(raw_year) and str(raw_year) not in ('', 'nan') else ''
        except (ValueError, TypeError):
            year = ''
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
    def _normalize_uri(raw_uri) -> Optional[str]:
        """Normalize a raw Letterboxd URI to canonical form. Returns None for short/unresolvable URLs."""
        if not raw_uri or (isinstance(raw_uri, float) and pd.isna(raw_uri)):
            return None
        return CSVParser.normalize_letterboxd_uri(str(raw_uri))

    @staticmethod
    def _process_ratings(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            movies_map[key] = {
                "title": row['Name'],
                "year": int(row['Year']) if pd.notna(row['Year']) else None,
                "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                "watched_date": DataProcessor._parse_date(row.get('Date')),
                "review": None,
                "is_watchlist": False,
                "is_liked": False,
                "is_watched": True,
                "watch_count": 1,
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
                    "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                    "rating": None,
                    "watched_date": None,
                    "review": None,
                    "is_watchlist": True,
                    "is_liked": False,
                    "is_watched": False,
                    "watch_count": 1,
                }
            else:
                movies_map[key]["is_watchlist"] = True

    @staticmethod
    def _process_likes(df: pd.DataFrame, movies_map: Dict):
        """
        Additive merge only — never overwrite fields already set by ratings/watched/diary.
        Runs LAST in the pipeline so is_liked is always preserved.
        """
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            if key in movies_map:
                # Additive: only set is_liked, never touch other fields
                movies_map[key]["is_liked"] = True
                if movies_map[key].get("rating") is None:
                    movies_map[key]["implicit_rating"] = 4.0
            else:
                # New entry — normalize URI (boxd.it short URLs → None, matched by title+year in upload.py)
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                    "rating": None,
                    "implicit_rating": 4.0,
                    "watched_date": None,
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": True,
                    "is_watched": True,
                    "watch_count": 1,
                }

    @staticmethod
    def _process_watched(df: pd.DataFrame, movies_map: Dict):
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            if key in movies_map:
                movies_map[key]["is_watched"] = True
                if not movies_map[key].get("watched_date"):
                    movies_map[key]["watched_date"] = DataProcessor._parse_date(row.get('Date'))
            else:
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                    "rating": None,
                    "watched_date": DataProcessor._parse_date(row.get('Date')),
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True,
                    "watch_count": 1,
                }

    @staticmethod
    def _process_diary(df: pd.DataFrame, movies_map: Dict):
        """
        Each row in diary.csv is one diary entry, including rewatches.
        Multiple rows for the same movie → increment watch_count.
        """
        for _, row in df.iterrows():
            key = DataProcessor._get_key(row)
            if not key: continue

            # Diary has 'Watched Date' which is the actual date watched, vs 'Date' which is log date
            watched_date = DataProcessor._parse_date(row.get('Watched Date')) or DataProcessor._parse_date(row.get('Date'))

            if key in movies_map:
                movies_map[key]["is_watched"] = True
                # Each diary entry = one watch; increment count for every entry beyond the first
                movies_map[key]["watch_count"] = movies_map[key].get("watch_count", 1) + 1
                # Use the most recent diary date
                if watched_date and (not movies_map[key].get("watched_date") or watched_date > movies_map[key]["watched_date"]):
                    movies_map[key]["watched_date"] = watched_date
                # Fill rating if diary has it and it's missing
                if pd.notna(row.get('Rating')) and movies_map[key].get("rating") is None:
                    movies_map[key]["rating"] = float(row['Rating'])
            else:
                movies_map[key] = {
                    "title": row['Name'],
                    "year": int(row['Year']) if pd.notna(row['Year']) else None,
                    "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                    "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                    "watched_date": watched_date,
                    "review": None,
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True,
                    "watch_count": 1,
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
                    "letterboxd_uri": DataProcessor._normalize_uri(row.get('Letterboxd URI')),
                    "rating": float(row['Rating']) if pd.notna(row['Rating']) else None,
                    "watched_date": DataProcessor._parse_date(row.get('Watched Date')) or DataProcessor._parse_date(row.get('Date')),
                    "review": str(review_text),
                    "is_watchlist": False,
                    "is_liked": False,
                    "is_watched": True,
                    "watch_count": 1,
                }
