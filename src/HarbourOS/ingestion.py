import os

import requests
from dotenv import load_dotenv

from HarbourOS.storage import insert_ais_messages

load_dotenv()

# Seconds to wait for BarentsWatch before giving up on a request. Without a
# limit, one stalled connection would hang a pipeline run until GitHub kills
# it hours later, and a killed run doesn't start the next one.
REQUEST_TIMEOUT = 60

# The live endpoint's default "Simple" model returns only position, speed,
# course, name and ship type. "Full" adds the navigational status and the
# crew-typed voyage fields (destination, ETA, IMO number, call sign). Until
# 2026-10-02 the pipeline asked for the default, so Bronze held none of them.
LATEST_PARAMS = {"modelType": "Full", "modelFormat": "Json"}


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
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise Exception(f"Failed to get token: {response.text}")

    token_data = response.json()
    return token_data["access_token"]


def fetch_ais_data(limit=None):
    """Fetch the latest AIS positions from BarentsWatch.

    `limit` exists only for tests and quick experiments. In normal use it stays
    None and every ship the API reports is kept -- a hard-coded cap silently
    discards ships as the fleet grows, which is how this pipeline spent its
    first two weeks seeing 100 ships out of 4,014.
    """
    access_token = get_access_token()
    api_url = "https://live.ais.barentswatch.no/v1/latest/combined"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(api_url, headers=headers, params=LATEST_PARAMS, timeout=REQUEST_TIMEOUT)
    print(f"API Status: {response.status_code}")

    if response.status_code != 200:
        print(f"Error fetching data: {response.status_code}")
        print(response.text)
        return []

    data = response.json()
    if not isinstance(data, list):
        return []

    return data if limit is None else data[:limit]


def fetch_historic_track(mmsi):
    """Fetch the last 24 hours of position history for one ship.

    Used for backfilling real, dense position data -- live polling only
    captures one snapshot per run, which isn't enough to see a full
    port-call journey unfold.
    """
    access_token = get_access_token()
    api_url = f"https://historic.ais.barentswatch.no/v1/historic/trackslast24hours/{mmsi}"
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        print(f"Error fetching historic track for {mmsi}: {response.status_code}")
        print(response.text)
        return []

    data = response.json()
    if isinstance(data, list):
        return data
    return []


def ingest_batch(limit=None):
    """Fetch and store a batch of AIS messages in one database write."""
    print("Fetching AIS positions...")
    positions = fetch_ais_data(limit=limit)
    print(f"Received {len(positions)} positions")

    stored = insert_ais_messages(positions)
    print(f"✅ Stored {stored} messages in Bronze layer")
    return stored


if __name__ == "__main__":
    ingest_batch()
