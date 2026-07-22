"""Curated symbol lists so the Advisor is always browsable.

Without a Groww token there is no full NSE/BSE list, and Binance may be briefly
unreachable — so we ship a hand-picked set of the most-traded names that work via
the Yahoo fallback. When a Groww token IS present, the /api/universe endpoint adds
the full ~7000-symbol list on top of these.
"""

# NIFTY-50-ish: the most liquid, recognisable NSE large-caps. All work on Yahoo (.NS).
POPULAR_STOCKS = [
    ("RELIANCE.NS", "Reliance Industries"), ("TCS.NS", "Tata Consultancy Services"),
    ("HDFCBANK.NS", "HDFC Bank"), ("INFY.NS", "Infosys"), ("ICICIBANK.NS", "ICICI Bank"),
    ("SBIN.NS", "State Bank of India"), ("BHARTIARTL.NS", "Bharti Airtel"),
    ("ITC.NS", "ITC"), ("LT.NS", "Larsen & Toubro"), ("HINDUNILVR.NS", "Hindustan Unilever"),
    ("BAJFINANCE.NS", "Bajaj Finance"), ("KOTAKBANK.NS", "Kotak Mahindra Bank"),
    ("AXISBANK.NS", "Axis Bank"), ("ASIANPAINT.NS", "Asian Paints"),
    ("MARUTI.NS", "Maruti Suzuki"), ("SUNPHARMA.NS", "Sun Pharma"),
    ("TATAMOTORS.NS", "Tata Motors"), ("TITAN.NS", "Titan"), ("WIPRO.NS", "Wipro"),
    ("ULTRACEMCO.NS", "UltraTech Cement"), ("NESTLEIND.NS", "Nestlé India"),
    ("ADANIENT.NS", "Adani Enterprises"), ("HCLTECH.NS", "HCL Technologies"),
    ("POWERGRID.NS", "Power Grid"), ("NTPC.NS", "NTPC"), ("ONGC.NS", "ONGC"),
    ("TATASTEEL.NS", "Tata Steel"), ("JSWSTEEL.NS", "JSW Steel"),
    ("COALINDIA.NS", "Coal India"), ("BAJAJFINSV.NS", "Bajaj Finserv"),
    ("HDFCAMC.NS", "HDFC AMC"), ("TECHM.NS", "Tech Mahindra"),
    ("INDUSINDBK.NS", "IndusInd Bank"), ("GRASIM.NS", "Grasim"),
    ("DRREDDY.NS", "Dr. Reddy's"), ("CIPLA.NS", "Cipla"), ("EICHERMOT.NS", "Eicher Motors"),
    ("BRITANNIA.NS", "Britannia"), ("DIVISLAB.NS", "Divi's Labs"),
    ("HEROMOTOCO.NS", "Hero MotoCorp"), ("BPCL.NS", "BPCL"),
    ("TATACONSUM.NS", "Tata Consumer"), ("APOLLOHOSP.NS", "Apollo Hospitals"),
    ("ADANIPORTS.NS", "Adani Ports"), ("HINDALCO.NS", "Hindalco"),
    ("SBILIFE.NS", "SBI Life"), ("BAJAJ-AUTO.NS", "Bajaj Auto"),
    ("M&M.NS", "Mahindra & Mahindra"), ("SHRIRAMFIN.NS", "Shriram Finance"),
    ("LTIM.NS", "LTIMindtree"),
]

# Top crypto by market cap — mapped to Binance USDT pairs at the edge (see binance.py).
POPULAR_CRYPTO = [
    ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("BNB-USD", "BNB"),
    ("SOL-USD", "Solana"), ("XRP-USD", "XRP"), ("ADA-USD", "Cardano"),
    ("DOGE-USD", "Dogecoin"), ("AVAX-USD", "Avalanche"), ("DOT-USD", "Polkadot"),
    ("MATIC-USD", "Polygon"), ("LINK-USD", "Chainlink"), ("LTC-USD", "Litecoin"),
    ("TRX-USD", "TRON"), ("SHIB-USD", "Shiba Inu"), ("UNI-USD", "Uniswap"),
    ("ATOM-USD", "Cosmos"), ("XLM-USD", "Stellar"), ("NEAR-USD", "NEAR Protocol"),
    ("ETC-USD", "Ethereum Classic"), ("FIL-USD", "Filecoin"), ("APT-USD", "Aptos"),
    ("ARB-USD", "Arbitrum"), ("OP-USD", "Optimism"), ("INJ-USD", "Injective"),
]
