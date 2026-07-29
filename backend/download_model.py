
import os
from sentence_transformers import SentenceTransformer

def download_model():
    model_name = "google/embeddinggemma-300m"
    cache_folder = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/models_cache")

    # login() IS required — do not "simplify" it away.
    #
    # It looks redundant (huggingface_hub reads HF_TOKEN from the env, and says
    # so in the build log), and one build did succeed without it. But the next
    # build failed with GatedRepoError 401 inside refresh_xet_connection_info:
    # the xet backend's short-lived read-token refresh does NOT reliably pick up
    # the env var, so a long download dies mid-transfer. Env-only auth is
    # non-deterministic here, which is worse than either outcome.
    #
    # login() persists the token to $HF_HOME/{token,stored_tokens}, which WOULD
    # ship inside the image. That is handled where it has to be handled — the
    # Dockerfile deletes those files in the SAME RUN layer that creates them
    # (a later layer cannot remove a file from an earlier one). See Dockerfile.
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

    # FAIL LOUDLY. This used to swallow the exception and print
    # "non-fatal, runtime will use volume cache", so a failed download still
    # exited 0 and the Docker build happily tagged an image whose
    # /models_cache was an empty 80K directory — looks fine, breaks at runtime.
    # That is exactly what happened on 2026-07-26 (GatedRepoError 401 on the
    # xet token refresh). A build that could not bake the model must not ship.
    SentenceTransformer(model_name, cache_folder=cache_folder)

    # Prove the weights actually landed — SentenceTransformer can return a
    # partially-populated cache dir without raising.
    size = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(cache_folder)
        for f in files
    )
    if size < 100 * 1024 * 1024:  # the model is ~1.2GB; 100MB is a generous floor
        raise RuntimeError(
            f"Model cache is only {size / 1024 / 1024:.1f}MB after download — "
            f"expected >100MB. Refusing to build an image without the model."
        )
    print(f"Model downloaded successfully ({size / 1024 / 1024:.0f}MB).")

if __name__ == "__main__":
    download_model()
