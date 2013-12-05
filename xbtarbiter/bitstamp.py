import requests
import hmac
import hashlib
import time

from decimal import Decimal


ORDER_OPEN = 'open'
ORDER_CLOSED = 'closed'


class BitstampException(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return str(self.msg)


class BitstampOrder(object):
    """ Bitstamp order.
    """
    def __init__(self, otype, oid):
        self.otype = otype
        self.oid = oid


class BitstampPlugin(object):
    """ Bitstamp.net plugin

    API documentation:
        https://www.bitstamp.net/api/
    """
    def __init__(self, client_id, key, secret):
        self.name = 'bitstamp.net'

        self._url = 'https://www.bitstamp.net/api'
        self._client_id = client_id
        self._key = key
        self._secret = secret

        self.refresh_account_info()

    def refresh_account_info(self):
        path = 'balance/'
        self._account_info = self._http_post(path)

    def refresh_order_book(self):
        path = 'order_book/'
        self._order_book = self._http_get(path)

    def refresh_orders(self):
        """ Refresh my orders.
        """
        # Refresh open orders
        path_open = 'open_orders/'
        self._open_orders = self._http_post(path_open)

        # Refresh closed orders. Note that this loads only the last 100 closed
        # orders in descending order by time - see api docs on how to load more
        path_closed = 'user_transactions/'
        self._closed_orders = self._http_post(path_closed)

    def create_bid_order(self, volume, price):
        """ Create a BID ("I want to buy") order.

        :return: Order ID
        """
        path = 'buy/'
        data = {
                'amount': "{0:.8f}".format(volume),
                'price': price,
            }
        response = self._http_post(path, data)
        order = BitstampOrder(otype='bid', oid=response['id'])
        return order

    def create_ask_order(self, volume, price):
        """ Create an ASK ("I want to sell") order.

        :return: Order ID
        """
        path = 'sell/'
        data = {
                'amount': "{0:.8f}".format(volume),
                'price': price,
            }
        response = self._http_post(path, data)
        order = BitstampOrder(otype='ask', oid=response['id'])
        return order

    def get_order_status(self, order):
        """ Get order status. Possible values: 'open', 'closed'. Raise exception
        if the order was not found.
        """
        # Refresh orders
        self.refresh_orders()

        # Check open orders
        for open_order in self._open_orders:
            if open_order['id'] == order.oid:
                return ORDER_OPEN

        # Check closed orders
        for closed_order in self._closed_orders:
            if closed_order['order_id'] == order.oid:
                return ORDER_CLOSED

        # If the order is not open nor closed it either does not exist or
        # was cancelled - we raise an exception
        raise BitstampException('Order not found')

    def cancel_order(self, order):
        """ Cancel an open order.
        """
        path = 'cancel_order/'
        data = {
                'id': order.oid,
            }
        response = self._http_post(path, data)
        if not response:
            raise BitstampException('Cancel order failed')


    @property
    def trade_fee(self):
        # Note: Fees are actually paid in USD from the trade value in USD
        # (probably, verify it to be sure)
        return Decimal(self._account_info['fee'])

    @property
    def open_orders(self):
        return self._open_orders

    @property
    def balance_xbt(self):
        return Decimal(self._account_info['btc_balance'])

    @property
    def balance_usd(self):
        return Decimal(self._account_info['usd_balance'])

    @property
    def avail_xbt(self):
        return Decimal(self._account_info['btc_available'])

    @property
    def avail_usd(self):
        return Decimal(self._account_info['usd_available'])

    @property
    def highest_bid(self):
        """ Return the highest bid from the order book.
        """
        bid = self._order_book['bids'][0]
        return {
                'price': Decimal(bid[0]),
                'volume': Decimal(bid[1]),
            }

    @property
    def lowest_ask(self):
        """ Return the lowest ask from the order book.
        """
        ask = self._order_book['asks'][0]
        return {
                'price': Decimal(ask[0]),
                'volume': Decimal(ask[1]),
            }

    def _sign(self, nonce):
        msg = '{0}{1}{2}'.format(nonce, self._client_id, self._key)
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest().upper()

    def _http_get(self, path):
        url = '{0}/{1}'.format(self._url, path)
        response = requests.get(url)
        if response.status_code != 200:
            msg = "\n".join(response.json()['error']['__all__'])
            raise BitstampException(msg)
        return response.json()

    def _http_post(self, path, data={}):
        url = '{0}/{1}'.format(self._url, path)
        nonce = str(int(time.time() * 1e6))

        payload = dict(data)
        payload['key'] = self._key
        payload['signature'] = self._sign(nonce)
        payload['nonce'] = nonce

        response = requests.post(url, data=payload)
        if response.status_code != 200:
            msg = "\n".join(response.json()['error']['__all__'])
            raise BitstampException(msg)

        response_json = response.json()
        if hasattr(response_json, 'has_key') and response_json.has_key('error'):
            raise BitstampException(response_json['error'])

        return response_json
