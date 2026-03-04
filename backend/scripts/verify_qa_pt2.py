import httpx
import json

def test_phase_5():
    print("--- PHASE 5.1 & 5.6: FEED API CHECK ---")
    headers = {"Content-Type": "application/json"}
    login_data = {"username": "QA_VecBox", "pin": "1234", "country_code": "ES"}
    
    with httpx.Client(base_url="http://localhost:8000", timeout=10.0) as client:
        res = client.post("/api/auth/login", json=login_data)
        if res.status_code != 200:
            print(f"Login failed: {res.text}")
            return
            
        token = res.json().get("token")
        print(f"Login successful.")
        res = client.get("/api/recommendations/feed", cookies={"vectorbox_token": token})
        if res.status_code != 200:
            print(f"Feed fetch failed: {res.text}")
            return
            
        feed = res.json()
        sections = feed.get("sections", [])
        print(f"Total sections found: {len(sections)}")
        for idx, s in enumerate(sections):
            print(f"{idx+1}. {s['title']}")
            if len(s['movies']) > 0:
                movie = s['movies'][0]
                providers = movie.get("providers", [])
                provider_names = [p['name'] for p in providers]
                print(f"   -> Example movie: {movie['title']} | Score: {movie.get('vectorbox_score')} | Providers visible: {len(provider_names) > 0} {provider_names}")
    
    print("\n--- PHASE 5.2: JAEGER TRACES CHECK ---")
    try:
        with httpx.Client(base_url="http://localhost:16686", timeout=5.0) as client:
            res = client.get("/api/traces?service=vectorbox-backend&limit=50")
            traces = res.json().get("data", [])
            spans_found = set()
            for t in traces:
                for s in t.get("spans", []):
                    name = s.get("operationName", "")
                    if "trident." in name.lower() or "signal_" in name.lower():
                        spans_found.add(name)
            
            print(f"Trident related spans found in last 50 traces: {len(spans_found)}")
            for s in sorted(list(spans_found)):
                print(f"- {s}")
    except Exception as e:
        print(f"Jaeger check failed: {e}")

if __name__ == "__main__":
    test_phase_5()
