import asyncio
import sys
import os
import logging
from datetime import datetime, timedelta
from sqlalchemy import select

# Añadir el root al path para poder importar módulos
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AsyncSessionLocal
from models.database import Movie
from services.movie_service import MovieService
from services.embedding_service import EmbeddingService
from services.qdrant_service import QdrantService
from services.tmdb_client import TMDBClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("heal_vectors")

# 30 Requests per Minute = 1 request cada 2 segundos.
# Ponemos 2.1s para ir sobre seguro.
GROQ_RATE_LIMIT_DELAY = 2.1

async def heal_recent_movies():
    logger.info("Iniciando escaneo de películas afectadas por el Rate Limit de Groq...")
    
    # Buscar películas creadas en los últimos 2 días
    time_threshold = datetime.utcnow() - timedelta(days=1)
    
    async with AsyncSessionLocal() as db:
        # Traer películas recientes
        result = await db.execute(
            select(Movie).where(Movie.created_at >= time_threshold).order_by(Movie.id.desc())
        )
        movies = result.scalars().all()
        
        if not movies:
            logger.info("No se encontraron películas recientes.")
            return

        logger.info(f"Se van a re-procesar {len(movies)} películas. Ritmo: 1 cada {GROQ_RATE_LIMIT_DELAY}s.")
        logger.info(f"Tiempo estimado: {round((len(movies) * GROQ_RATE_LIMIT_DELAY) / 60, 2)} minutos.")
        
        tmdb = TMDBClient()
        movie_service = MovieService(db, tmdb=tmdb)
        
        success_count = 0
        
        for i, movie in enumerate(movies, 1):
            logger.info(f"[{i}/{len(movies)}] Curando vector de: {movie.title} (TMDB: {movie.tmdb_id})")
            
            try:
                # 1. Borramos la flag temporalmente si usamos la misma función
                # o forzamos el enriquecimiento. Dependiendo de cómo implementaste 
                # la Mejora 1, el método enrich_movie de MovieService debería tener un flag.
                # Si implementaste un flag 'force', úsalo. Si no, lo llamamos directamente:
                
                # Asumimos que tu MovieService tiene la lógica de llamar a cinematic_enricher
                # y luego a qdrant.upsert_movie_vector. Forzamos la actualización:
                await movie_service.enrich_movie(movie, skip_qdrant=False)
                
                success_count += 1
                
            except Exception as e:
                logger.error(f"Fallo curando {movie.title}: {e}")
            
            # EL FRENO DE MANO VITAL
            await asyncio.sleep(GROQ_RATE_LIMIT_DELAY)
            
        await movie_service.close()
        logger.info(f"¡Proceso terminado! {success_count}/{len(movies)} vectores curados exitosamente.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(heal_recent_movies())