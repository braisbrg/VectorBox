
import os
import shutil
from sentence_transformers import SentenceTransformer

def download_model():
    model_name = "all-MiniLM-L6-v2"
    # We set this in Dockerfile
    cache_folder = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/models_cache")
    
    print(f"Downloading {model_name} to {cache_folder}...")
    
    if not os.path.exists(cache_folder):
        os.makedirs(cache_folder)
        
    # This triggers the download
    model = SentenceTransformer(model_name, cache_folder=cache_folder)
    print("Model downloaded successfully.")

    # Cleanup temp files if any (sentence-transformers usually handles this well)

if __name__ == "__main__":
    download_model()
