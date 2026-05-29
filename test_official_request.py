import requests

token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJBaDhKSnlHRkJyNGZiRjNhTHlrTkhSbkpQTG5DS2ZFRSIsImV4cCI6MTc3OTQzNDExOSwibmJmIjoxNzc5NDMyMzE0fQ.5V_mrMFnrC1H7czKe8o7BRK98UhuoZIMQ1xemHOAu4s"
url = "https://api.klingai.com/v1/videos/image2video"
r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"model_name": "kling-v1", "image": "test", "prompt": "test"})
print(r.status_code, r.text)
