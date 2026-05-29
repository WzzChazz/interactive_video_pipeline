import urllib.request
import requests

url = "https://s3.siliconflow.cn/temporary/None/2ebh7o1e4sr33_9802eb800fec6db66d51e616edc9f757_b9cdc145_4e30cdab_00001_.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAXXXXFILESEXAMPLE%2F20260522%2Fcn-shanghai-1%2Fs3%2Faws4_request&X-Amz-Date=20260522T070117Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Security-Token=eyJhbGciOiJSUzI1NiIsImtpZCI6ImdyYXkiLCJ0eXAiOiJKV1QifQ.eyJzdWIiOiJmYWFzOmRlcDpvcHI6ZDI5Y3UzZ2gzdnZjNzNjNWJpNWc6MTJjNjMiLCJpc3MiOiJodHRwczovL2lhbS5zaWxpY29uZmxvdy5jbiIsImlhdCI6MTc3OTQyNTU2OCwiZXhwIjoxNzc5NTk4MzY4LCJ0eXAiOiJzZXJ2aWNlIiwiYXBsdCI6InNmOmZhYXM6ZmFicmljIiwidG50IjoiZDI5Y3UzZ2gzdnZjNzNjNWJpNWciLCJpZCI6ImQ4N3UyODZjbm5jYzczOWNpYTZnIiwiYWNjZXNzIjpbeyJ0eXBlIjoiZmFhcyIsInN1YmplY3RJZCI6ImQyOWN1M2doM3Z2YzczYzViaTVnIiwiYWN0aW9ucyI6WyJmYWFzOmludm9jYXRpb246cHVsbGluZyJdfV19.Pur9D2VdRX5MMaxuzQgnn3TvS_DRYtmanHGbsUJ6Ip5IqFKstsdW9hbowv_lZ8fBOTKwvBDbrmTH_HVirVwyWol70cCxpxlFunLUU8qZv5V0yicx7L_99z5S8zr7jo70fRClP5TJrMfiaSc2dR7DwdWo43BNseDnNgInn-HkXaVH0zoGORPD1wa3Wdg6gJgN_YZruMVaC7rO188JY34CnPRkvrdzM0IdMmUKUVHuZa1JcwB0e8I1enLw-R5qvtJDwNpHTECCkyWdah6WTdjUD5kvVVuhzzChZhqA0X8P3cqEMHz1iG6DeMGgxAkElF8FUh-m715r7TxopiscIsNb3RhwBREOiAJln4EmXOJEeRi_MGUfjp3eK8Lp5aZpqcO53lHKZ9fJOD4zt5en72af0Ae52vTHlQtfnSYVxhoiMxVAQ9FSi8x7mTc5CCVeSn9LLNcpLXejO6whAc9iaJ491DCb6WpJjMPQNhVW-HtO0K69WbfwdiPFyvL8M-eWQHeIjzBCQ5x8NAX4Qz9NcX52kIC3L0N-wmL8XQaIfgHbJxY5-o6jUso0oVkRUCOHYMnLaPP39exW_hiYapnvVjO6k9NgCs6epCUa5jXhXzrrKL3AOUX9AvZnWWPOZcARTXlKlf3G9mBY3Le6W14dRFw0sSx-aXagSaMeoknFNh64yaM&X-Amz-Signature=b0e7a2b9ed3715c9b13928178e2df830c2c1995805f96ddfc6ef1e469d414a9a"

try:
    print("Testing urllib...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        print("urllib code:", response.getcode())
except Exception as e:
    print("urllib exception:", e)

try:
    print("Testing requests...")
    r = requests.get(url, timeout=5)
    print("requests code:", r.status_code)
except Exception as e:
    print("requests exception:", e)
