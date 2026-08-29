import os

import requests
from dotenv import load_dotenv

load_dotenv()

client_id = os.getenv("BARENTSWATCH_CLIENT_ID")
client_secret = os.getenv("BARENTSWATCH_CLIENT_SECRET")

token_url = "https://id.barentswatch.no/connect/token"

response = requests.post(
    token_url,
    data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "ais",
    },
)

print(f"Status Code: {response.status_code}")
print(f"Response: {response.text}")

if response.status_code == 200:
    token_data = response.json()
    access_token = token_data.get("access_token")
    print("\n✅ SUCCESS! Your access token:")
    print(f"{access_token}")
else:
    print("\n❌ FAILED")
