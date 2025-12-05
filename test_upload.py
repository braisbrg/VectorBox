import requests
import time
import sys
import os

BASE_URL = "http://localhost:8000"
FILE_PATH = "ratings.csv"

def wait_for_health():
    print("Waiting for API to be healthy...")
    for i in range(30):
        try:
            response = requests.get(f"{BASE_URL}/health")
            if response.status_code == 200:
                print("API is healthy!")
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    print("API failed to become healthy.")
    return False

def upload_ratings():
    print(f"Uploading {FILE_PATH}...")
    if not os.path.exists(FILE_PATH):
        print(f"File {FILE_PATH} not found!")
        return None

    with open(FILE_PATH, "rb") as f:
        files = {"file": (FILE_PATH, f, "text/csv")}
        response = requests.post(f"{BASE_URL}/api/upload/ratings", files=files, params={"user_id": 1})
    
    if response.status_code == 200:
        print("Upload successful!")
        return response.json()
    else:
        print(f"Upload failed: {response.status_code} - {response.text}")
        return None

def get_recommendations():
    print("Fetching clusters...")
    # Poll for clusters until they are generated
    cluster_id = None
    for i in range(60): # Wait up to 2 minutes
        try:
            response = requests.get(f"{BASE_URL}/api/recommendations/clusters/1")
            if response.status_code == 200:
                clusters = response.json()
                if clusters:
                    print(f"Found {len(clusters)} clusters!")
                    cluster_id = clusters[0]['cluster_id']
                    print(f"Using cluster: {clusters[0]['label']} (ID: {cluster_id})")
                    break
        except:
            pass
        if i % 5 == 0:
            print("Waiting for clusters to be generated...")
        time.sleep(2)
    
    if cluster_id is None:
        print("Timed out waiting for clusters.")
        return

    print("Fetching recommendations for mood...")
    response = requests.post(f"{BASE_URL}/api/recommendations/by-mood", json={
        "user_id": 1,
        "cluster_id": cluster_id,
        "limit": 5
    })
    
    if response.status_code == 200:
        data = response.json()
        print(f"Got {len(data)} recommendations:")
        for item in data:
            movie = item['movie']
            print(f"- {movie['title']} ({movie['year']}) - Score: {item['similarity_score']:.2f}")
    else:
        print(f"Failed to get recommendations: {response.status_code} - {response.text}")

def main():
    if not wait_for_health():
        sys.exit(1)
    
    upload_result = upload_ratings()
    if not upload_result:
        sys.exit(1)
    
    print(f"Upload response: {upload_result}")
    
    get_recommendations()

if __name__ == "__main__":
    main()
