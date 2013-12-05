import requests
import hmac
import hashlib
import time
import base64
import urllib

from decimal import Decimal


ORDER_OPEN = 'open'
ORDER_CLOSED = 'closed'


class KrakenException(Exception):
    def __init__(self, error):
        self.errors = error

    def __str__(self):
        return "\n".join(self.errors)


class KrakenOrder(object):
    """ Kraken order.
    """
    def __init__(self, otype, oid):
        self.otype = otype
        self.oid = oid


class KrakenPlugin(object):
    """ Kraken.com plugin

    Note: At Kraken we actually trade in XBT/EUR, but we need to re-calculate
    prices to USD so they are comparable to what other plugins work with.

    API documentation:
        https://www.kraken.com/help/api

    :param key: API key
    :param secret: API secret
    :param eurusd_rate: current EUR/USD exchange rate
    """
    def __init__(self, key, secret, eurusd_rate):
        self.name = 'kraken.com [EUR]'

        self._url = 'https://api.kraken.com'
        self._version = '0'
        self._key = key
        self._secret = secret
        self._eurusd_rate = eurusd_rate
        
        self.refresh_account_info()

    def refresh_account_info(self):
        path_balance = 'private/Balance'
        self._account_info = self._http_post(path_balance)

        path_trade_volume = 'private/TradeVolume'
        self._trade_volume = self._http_post(path_trade_volume,
                { 'pair': 'XXBTZEUR' })

    def refresh_order_book(self):
        path = 'public/Depth'
        result = self._http_get(path, {
                'pair': 'XXBTZEUR',
                'count': 1,
            })
        self._order_book = result['XXBTZEUR']

    def refresh_orders(self):
        """ Refresh my orders.
        """
        path_open = 'private/OpenOrders'
        result = self._http_post(path_open)
        self._open_orders = result['open']

        path_closed = 'private/ClosedOrders'
        result = self._http_post(path_closed)
        self._closed_orders = result['closed']

    def create_bid_order(self, volume, price):
        """ Create a BID ("I want to buy") order.

        :param volume: Volume in XBT
        :param price: Price in USD
        :return: Order ID
        """
        path = 'private/AddOrder'
        price_eur = price / self._eurusd_rate
        data = {
                'pair': 'XXBTZEUR',
                'type': 'buy',
                'ordertype': 'limit',
                'price': price_eur,
                'volume': "{0:.8f}".format(volume),
            }
        response = self._http_post(path, data)
        order = KrakenOrder(otype='bid', oid=response['txid'][0])
        return order

    def create_ask_order(self, volume, price):
        """ Create an ASK ("I want to sell") order.

        :param volume: Volume in XBT
        :param price: Price in USD
        :return: Order ID
        """
        path = 'private/AddOrder'
        price_eur = price / self._eurusd_rate
        data = {
                'pair': 'XXBTZEUR',
                'type': 'sell',
                'ordertype': 'limit',
                'price': price_eur,
                'volume': "{0:.8f}".format(volume),
            }
        response = self._http_post(path, data)
        order = KrakenOrder(otype='ask', oid=response['txid'][0])
        return order

    def get_order_status(self, order):
        """ Get order status. Possible values: 'open', 'closed'. Raise exception
        if the order was not found.
        """
        # Refresh orders
        self.refresh_orders()

        # Check open orders
        for oid in self._open_orders.keys():
            if oid == order.oid:
                status = self._open_orders[oid]['status']
                if status in ('open', 'pending'):
                    return ORDER_OPEN
                elif status == 'cancelled':
                    raise BitstampException('Order was cancelled')
                elif status == 'expired':
                    raise BitstampException('Order expired')
                else:
                    raise BitstampException('Unexpected order status: {0}'.format(status))

        # Check closed orders
        for oid in self._closed_orders.keys():
            if oid == order.oid:
                status = self._closed_orders[oid]['status']
                if status == 'closed':
                    return ORDER_CLOSED
                else:
                    raise BitstampException('Unexpected order status: {0}'.format(status))

        # If the order is not open nor closed it does not exist - we raise an
        # exception
        raise BitstampException('Order not found')

    def cancel_order(self, order):
        """ Cancel open order.
        """
        path = 'private/CancelOrder'
        data = {
                'txid': order.oid,
            }
        self._http_post(path, data)

    @property
    def trade_fee(self):
        return Decimal(self._trade_volume['fees']['XXBTZEUR']['fee'])

    @property
    def open_orders(self):
        return self._open_orders

    @property
    def balance_xbt(self):
        if self._account_info.has_key('XXBT'):
            return Decimal(self._account_info['XXBT'])
        else:
            return Decimal('0.0')

    @property
    def balance_usd(self):
        if self._account_info.has_key('ZEUR'):
            balance_eur = Decimal(self._account_info['ZEUR'])
            return balance_eur * self._eurusd_rate
        else:
            return Decimal('0.0')

    @property
    def avail_xbt(self):
        return self.balance_xbt

    @property
    def avail_usd(self):
        return self.balance_usd

    @property
    def highest_bid(self):
        """ Return the highest bid from the order book.
        """
        (price_eur, volume, _) = self._order_book['bids'][0]
        price_usd = Decimal(price_eur) * self._eurusd_rate
        return {
                'price': Decimal(price_usd),
                'volume': Decimal(volume),
            }

    @property
    def lowest_ask(self):
        """ Return the lowest ask from the order book.
        """
        (price_eur, volume, _) = self._order_book['asks'][0]
        price_usd = Decimal(price_eur) * self._eurusd_rate
        return {
                'price': Decimal(price_usd),
                'volume': Decimal(volume),
            }

    def _sign(self, path, nonce, data):
        """ Create a signature for private requests.
        """
        url = '/{0}/{1}'.format(self._version, path)
        urlencoded_data = urllib.urlencode(data)
        msg = url + hashlib.sha256(str(nonce) + urlencoded_data).digest()
        signature = hmac.new(base64.b64decode(self._secret), msg,
                hashlib.sha512)
        return base64.b64encode(signature.digest())

    def _http_get(self, path, params=None):
        url = '{0}/{1}/{2}'.format(self._url, self._version, path)
        response = requests.get(url, params=params)
        result = response.json()
        if response.status_code != 200 or result['error']:
            raise KrakenException(result['error'])
        return response.json()['result']

    def _http_post(self, path, data={}):
        url = '{0}/{1}/{2}'.format(self._url, self._version, path)

        nonce = int(time.time() * 1e3)
        payload = dict(data)
        payload['nonce'] = nonce

        headers = {
                'API-Key': self._key,
                'API-Sign': self._sign(path, nonce, payload),
            }

        response = requests.post(url, data=payload, headers=headers)
        result = response.json()
        if response.status_code != 200 or result['error']:
            raise KrakenException(result['error'])
        return result['result']
