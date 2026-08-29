import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

client_id = os.getenv("BARENTSWATCH_CLIENT_ID")
client_secret = os.getenv("BARENTSWATCH_CLIENT_SECRET")

# Get token
print("Getting access token...")
response = requests.post(
    "https://id.barentswatch.no/connect/token",
    data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "ais",
    },
)

token = response.json()["access_token"]
print("✅ Got token\n")

# Get one position
print("Fetching AIS data...")
response = requests.get(
    "https://live.ais.barentswatch.no/v1/latest/combined",
    headers={"Authorization": f"Bearer {token}"},
)

data = response.json()
print("✅ Got data\n")

print("First vessel data:")
print(json.dumps(data[0], indent=2))
