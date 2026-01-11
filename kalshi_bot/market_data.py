"""Market data fetching functions"""
import requests
import aiohttp
from auth import get_auth_headers
from config import REST_URL_BASE

async def get_market_orderbook_async(session, ticker):
    """Get current bid/ask for a market (async version)"""
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
            if resp.status == 200:
                market_data = (await resp.json()).get('market', {})
                return {
                    'yes_bid': market_data.get('yes_bid', 0) or 0,
                    'yes_ask': market_data.get('yes_ask', 100) or 100,
                    'no_bid': market_data.get('no_bid', 0) or 0,
                    'no_ask': market_data.get('no_ask', 100) or 100,
                    'last_price': market_data.get('last_price', 0)
                }
    except:
        pass
    return None

def get_market_orderbook(ticker):
    """Get current bid/ask for a market (sync version for trade checking)"""
    try:
        url = f"{REST_URL_BASE}/{ticker}"
        headers = get_auth_headers('GET', f'/trade-api/v2/markets/{ticker}')
        resp = requests.get(url, headers=headers, timeout=1.5)
        
        if resp.status_code == 200:
            market_data = resp.json().get('market', {})
            return {
                'yes_bid': market_data.get('yes_bid', 0) or 0,
                'yes_ask': market_data.get('yes_ask', 100) or 100,
                'no_bid': market_data.get('no_bid', 0) or 0,
                'no_ask': market_data.get('no_ask', 100) or 100,
                'last_price': market_data.get('last_price', 0)
            }
    except:
        pass
    return None


