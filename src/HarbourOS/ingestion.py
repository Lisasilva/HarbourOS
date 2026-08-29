import os

import requests
from dotenv import load_dotenv

from HarbourOS.storage import insert_ais_message

load_dotenv()


def get_access_token():
    """Get a fresh access token from BarentsWatch"""

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

    if response.status_code != 200:
        raise Exception(f"Failed to get token: {response.text}")

    token_data = response.json()
    return token_data["access_token"]


def fetch_ais_data(limit=100):
    """Fetch latest AIS positions from BarentsWatch API"""

    access_token = get_access_token()

    api_url = "https://live.ais.barentswatch.no/v1/latest/combined"

    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(api_url, headers=headers)

    print(f"API Status: {response.status_code}")

    if response.status_code != 200:
        print(f"Error fetching data: {response.status_code}")
        print(response.text)
        return []

    # Parsing the JSON response
    data = response.json()

    # The response is already a list of positions
    # If limit is specified, take only the first 'limit' items
    if isinstance(data, list):
        positions = data[:limit]  # Take only first 'limit' vessels
    else:
        positions = []

    return positions


def ingest_batch(limit=100):
    """Fetch and store a batch of AIS messages"""

    print(f"Fetching up to {limit} AIS positions...")
    positions = fetch_ais_data(limit=limit)

    print(f"Received {len(positions)} positions")

    # Store each position in the database
    for position in positions:
        try:
            insert_ais_message(position)
        except Exception as e:
            print(f"Error inserting position: {e}")
            continue

    print(f"✅ Stored {len(positions)} messages in Bronze layer")


if __name__ == "__main__":
    ingest_batch(limit=100)
