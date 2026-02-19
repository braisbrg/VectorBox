"""
CSV Parser for Letterboxd exports
Security: File validation, size limits, sanitization
"""
import pandas as pd
import io
from typing import List, Dict, Tuple
from fastapi import UploadFile, HTTPException
import logging
from datetime import datetime
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class CSVParser:
    """Parse and validate Letterboxd CSV files"""
    
    # Security: File size limits (10MB max)
    MAX_FILE_SIZE = 10 * 1024 * 1024
    
    # Expected columns for validation
    RATINGS_COLUMNS = {"Name", "Year", "Letterboxd URI"}
    WATCHED_COLUMNS = {"Name", "Year", "Letterboxd URI"}
    
    @staticmethod
    async def validate_file(file: UploadFile) -> bytes:
        """
        Validate uploaded file
        Security: Check file type, size, and content
        """
        # Security: Validate file extension
        if not file.filename.endswith('.csv'):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Only CSV files are allowed."
            )
        
        # Security: Read file with size limit
        content = await file.read()
        if len(content) > CSVParser.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {CSVParser.MAX_FILE_SIZE // 1024 // 1024}MB"
            )
        
        # Security: Validate it's actually a CSV
        try:
            pd.read_csv(io.BytesIO(content), nrows=1)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail="Invalid CSV format"
            )
        
        return content
    
    @staticmethod
    async def parse_ratings_csv(file: UploadFile) -> Tuple[List[Dict], List[str]]:
        """
        Parse Letterboxd ratings.csv
        Returns: (movies_data, errors)
        """
        content = await CSVParser.validate_file(file)
        errors = []
        movies = []
        
        try:
            df = pd.read_csv(io.BytesIO(content))
            
            # Validate required columns
            if not CSVParser.RATINGS_COLUMNS.issubset(df.columns):
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required columns. Expected: {CSVParser.RATINGS_COLUMNS}"
                )
            
            # Process each row
            for idx, row in df.iterrows():
                try:
                    # Security: Sanitize and validate data
                    title = BeautifulSoup(str(row.get("Name", "")), "html.parser").get_text().strip()[:500]
                    year = row.get("Year")
                    letterboxd_uri = BeautifulSoup(str(row.get("Letterboxd URI", "")), "html.parser").get_text().strip()[:500]
                    rating = row.get("Rating")
                    watched_date = row.get("Watched Date")
                    
                    # Security: Review needs to be text-only
                    review_raw = row.get("Review", "")
                    review = BeautifulSoup(str(review_raw), "html.parser").get_text().strip() if pd.notna(review_raw) else ""
                    
                    # Validate required fields
                    if not title:
                        errors.append(f"Row {idx}: Missing title")
                        continue
                    
                    # Parse year
                    try:
                        year = int(year) if pd.notna(year) else None
                        if year and (year < 1800 or year > 2100):
                            errors.append(f"Row {idx}: Invalid year {year}")
                            year = None
                    except (ValueError, TypeError):
                        year = None
                    
                    # Parse rating
                    try:
                        rating = float(rating) if pd.notna(rating) else None
                        if rating and (rating < 0 or rating > 5):
                            errors.append(f"Row {idx}: Invalid rating {rating}")
                            rating = None
                    except (ValueError, TypeError):
                        rating = None
                    
                    # Parse date
                    try:
                        if pd.notna(watched_date):
                            watched_date = pd.to_datetime(watched_date).to_pydatetime()
                        else:
                            watched_date = None
                    except (ValueError, TypeError):
                        watched_date = None
                    
                    # Security: Limit review length
                    if pd.notna(review):
                        review = str(review)[:5000]
                    else:
                        review = None
                    
                    movies.append({
                        "title": title,
                        "year": year,
                        "letterboxd_uri": letterboxd_uri,
                        "rating": rating,
                        "watched_date": watched_date,
                        "review": review
                    })
                    
                except Exception as e:
                    errors.append(f"Row {idx}: {str(e)}")
                    logger.error(f"Error parsing row {idx}: {e}")
            
            logger.info(f"Parsed {len(movies)} movies from ratings CSV")
            return movies, errors
            
        except Exception as e:
            logger.error(f"Failed to parse ratings CSV: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to parse CSV: {str(e)}"
            )
    
    @staticmethod
    async def parse_watched_csv(file: UploadFile) -> Tuple[List[Dict], List[str]]:
        """
        Parse Letterboxd watched.csv
        Similar to ratings but may have different columns
        """
        content = await CSVParser.validate_file(file)
        errors = []
        movies = []
        
        try:
            df = pd.read_csv(io.BytesIO(content))
            
            # Validate required columns
            if not CSVParser.WATCHED_COLUMNS.issubset(df.columns):
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing required columns. Expected: {CSVParser.WATCHED_COLUMNS}"
                )
            
            # Process each row
            for idx, row in df.iterrows():
                try:
                    title = str(row.get("Name", "")).strip()[:500]
                    year = row.get("Year")
                    letterboxd_uri = str(row.get("Letterboxd URI", "")).strip()[:500]
                    watched_date = row.get("Watched Date")
                    
                    if not title:
                        errors.append(f"Row {idx}: Missing title")
                        continue
                    
                    # Parse year
                    try:
                        year = int(year) if pd.notna(year) else None
                        if year and (year < 1800 or year > 2100):
                            year = None
                    except (ValueError, TypeError):
                        year = None
                    
                    # Parse date
                    try:
                        if pd.notna(watched_date):
                            watched_date = pd.to_datetime(watched_date).to_pydatetime()
                        else:
                            watched_date = None
                    except (ValueError, TypeError):
                        watched_date = None
                    
                    movies.append({
                        "title": title,
                        "year": year,
                        "letterboxd_uri": letterboxd_uri,
                        "watched_date": watched_date
                    })
                    
                except Exception as e:
                    errors.append(f"Row {idx}: {str(e)}")
            
            logger.info(f"Parsed {len(movies)} movies from watched CSV")
            return movies, errors
            
        except Exception as e:
            logger.error(f"Failed to parse watched CSV: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to parse CSV: {str(e)}"
            )
