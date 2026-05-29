import requests
from email.utils import parsedate_to_datetime

r = requests.get("https://baidu.com", timeout=5)
server_date = r.headers.get('Date')
if server_date:
    real_time = int(parsedate_to_datetime(server_date).timestamp())
    print("Real Timestamp:", real_time)
