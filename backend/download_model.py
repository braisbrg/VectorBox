
import os
from sentence_transformers import SentenceTransformer

def download_model():
    model_name = "google/embeddinggemma-300m"
    cache_folder = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/models_cache")

    # NO huggingface_hub.login() here. It PERSISTS the token to
    # $HF_HOME/token + stored_tokens (~/.cache/huggingface/), which becomes a
    # layer in the image — defeating the whole point of passing HF_TOKEN via a
    # BuildKit secret mount instead of a build ARG. Verified 2026-07-25: the
    # token was sitting in /root/.cache/huggingface/token inside the built
    # image, readable by anyone who can `docker save` or pull it.
    #
    # It was also redundant: huggingface_hub reads HF_TOKEN from the environment
    # on its own and prefers it over the stored file — the build log said so
    # ("Environment variable `HF_TOKEN` ... is the current active token
    # independently from the token you've just configured"). Gated model
    # downloads authenticate fine with the env var alone.
    if os.environ.get("HF_TOKEN"):
        print("HF_TOKEN present — huggingface_hub will use it from the environment.")

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
