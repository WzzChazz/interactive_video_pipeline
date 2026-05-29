import requests
import time
from email.utils import parsedate_to_datetime

r = requests.get("https://api.klingai.com/v1/videos/image2video/test", timeout=5)
server_date = r.headers.get('Date')
print("Server Date:", server_date)
if server_date:
    server_time = int(parsedate_to_datetime(server_date).timestamp())
    local_time = int(time.time())
    print("Server Timestamp:", server_time)
    print("Local Timestamp:", local_time)
    print("Offset:", local_time - server_time)
