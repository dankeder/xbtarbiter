import requests
from decimal import Decimal

def get_eurusd():
    """ Return the current EUR/USD exchange rate from Yahho Finance API.
    
    :return: Current EUR/USD exchange rate
    """
    # See:
    #   http://code.google.com/p/yahoo-finance-managed/wiki/csvQuotesDownload
    #   http://code.google.com/p/yahoo-finance-managed/wiki/enumQuoteProperty
    url = 'http://download.finance.yahoo.com/d/quotes.csv'
    params = {
            'f': 'l1',
            's': 'EURUSD=X',
        }
    response = requests.get(url, params=params)
    if response.status_code != 200:
        raise Exception('Failed to get EURUSD exchange rate')
    return Decimal(response.text)
