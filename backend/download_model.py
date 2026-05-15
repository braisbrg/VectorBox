
import os
from sentence_transformers import SentenceTransformer

def download_model():
    model_name = "google/embeddinggemma-300m"
    cache_folder = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/models_cache")

    hf_token = os.environ.get("HF_TOKEN")
    if hf_token:
        try:
            from huggingface_hub import login
            login(token=hf_token, add_to_git_credential=False)
            print("Logged in to HuggingFace Hub.")
        except Exception as e:
            print(f"HF login failed (non-fatal): {e}")

    print(f"Downloading {model_name} to {cache_folder}...")
    if not os.path.exists(cache_folder):
        os.makedirs(cache_folder)

    try:
        SentenceTransformer(model_name, cache_folder=cache_folder)
        print("Model downloaded successfully.")
    except Exception as e:
        print(f"Model download failed (non-fatal, runtime will use volume cache): {e}")

if __name__ == "__main__":
    download_model()
