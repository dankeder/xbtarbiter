import requests
import hmac
import base64
import hashlib
import time
import urllib

from decimal import Decimal


ORDER_OPEN = 'open'
ORDER_CLOSED = 'closed'


class MtgoxException(Exception):
    def __init__(self, response):
        self.token = response['token']
        self.error = response['error']

    def __str__(self):
        return '[{0}] {1}'.format(self.token, self.error)


class MtgoxOrder(object):
    """ Mtgox.com order.
    """
    def __init__(self, otype, oid):
        self.otype = otype
        self.oid = oid


class MtgoxPlugin(object):
    """ Mtgox.com plugin

    API documentation:
        https://bitbucket.org/nitrous/mtgox-api/overview#markdown-header-background
    """
    def __init__(self, key, secret):
        self.name = 'mtgox.net'

        self._url = 'https://data.mtgox.com/api/2'
        self._key = key
        self._secret = secret

        self.refresh_account_info()

    def refresh_account_info(self):
        """ Refresh account info
        """
        path = 'BTCUSD/money/info'
        self._account_info = self._http_post(path)

    def refresh_order_book(self):
        """ Refresh the order book
        """
        path = 'BTCUSD/money/depth/fetch'
        self._order_book = self._http_get(path)

    def refresh_orders(self):
        """ Refresh my orders.
        """
        path = 'BTCUSD/money/orders'
        response = self._http_post(path)
        if response['result'] != 'success':
            raise MtgoxException(response)
        self._open_orders = response['data']

    def create_bid_order(self, volume, price):
        """ Create a BID ("I want to buy") order.

        :return: Order ID
        """
        path = 'BTCUSD/money/order/add'
        data = {
                'type': 'bid',
                'amount_int': volume * Decimal('1e8'),
                'price_int': price * Decimal('1e5'),
            }
        response = self._http_post(path, data)
        if response['result'] != 'success':
            raise MtgoxException(response)
        order = MtgoxOrder(otype='bid', oid=response['data'])

        # Make sure the order was not cancelled automatically. The
        # get_order_status() method will raise exception if the order was not
        # found amongst open or closed orders.
        self.get_order_status(order)

        return order

    def create_ask_order(self, volume, price):
        """ Create an ASK ("I want to sell") order.

        :return: Order ID
        """
        path = 'BTCUSD/money/order/add'
        data = {
                'type': 'ask',
                'amount_int': volume * Decimal('1e8'),
                'price_int': price * Decimal('1e5'),
            }
        response = self._http_post(path, data)
        if response['result'] != 'success':
            raise MtgoxException(response)
        order = MtgoxOrder(otype='ask', oid=response['data'])

        # Make sure the order was not cancelled automatically. The
        # get_order_status() method will raise exception if the order was not
        # found amongst open or closed orders.
        self.get_order_status(order)

        return order

    def get_order_status(self, order):
        """ Get order status. Possible values: 'open', 'closed'. Raise exception
        if the order was not found.
        """
        # Check open orders
        while True:
            self.refresh_orders()
            orderinfo = self._get_open_order(order.oid)
            if orderinfo is not None:
                if orderinfo['status'] in ('pending', 'executing', 'post-pending'):
                    time.sleep(self._get_order_lag())
                    continue
                elif orderinfo['status'] == 'open':
                    return ORDER_OPEN
            else:
                break

        # Check fulfilled (closed) orders
        order_result = self._get_order_result(order)
        if order_result is not None:
            return ORDER_CLOSED

        # If the order is not open nor closed it either does not exist or
        # was cancelled - we raise an exception
        raise MtgoxException({
                'token': 'order_notfound',
                'error': 'Order not found',
            })

    def cancel_order(self, order):
        """ Cancel an open order.
        """
        path = 'BTCUSD/money/order/cancel'
        data = {
                'oid': order.oid,
            }
        response = self._http_post(path, data)
        if response['result'] != 'success':
            raise MtgoxException(response)

    @property
    def trade_fee(self):
        # Note: Fees are actually paid in XBT from the trade value in XBT.
        # But I calculate the fee in USD from the trade value in USD.
        return Decimal(str(self._account_info['data']['Trade_Fee']))

    @property
    def open_orders(self):
        return self._open_orders

    @property
    def balance_xbt(self):
        return Decimal(self._account_info['data']['Wallets']['BTC']['Balance']['value_int']) / Decimal('1e8')

    @property
    def balance_usd(self):
        return Decimal(self._account_info['data']['Wallets']['USD']['Balance']['value_int']) / Decimal('1e5')

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
        bid = max(self._order_book['data']['bids'],
                key=lambda x: Decimal(x['price_int']))
        return {
                'price': Decimal(bid['price_int']) / Decimal('1e5'), # XXX: 1e5 works only for USD!
                'volume': Decimal(bid['amount_int']) / Decimal('1e8'),
            }

    @property
    def lowest_ask(self):
        """ Return the lowest ask from the order book.
        """
        ask = min(self._order_book['data']['asks'],
                key=lambda x: x['price_int'])
        return {
                'price': Decimal(ask['price_int']) / Decimal('1e5'), # XXX: 1e5 works only for USD!
                'volume': Decimal(ask['amount_int']) / Decimal('1e8'),
            }

    def _get_open_order(self, order_id):
        """ Get information about an (open) order. Return None if no order is
        found.
        """
        for order in self._open_orders:
            if order['oid'] == order_id:
                return order
        return None

    def _sign(self, path, data):
        """ Create a signature for private requests.
        """
        urlencoded_data = urllib.urlencode(data)
        mac = hmac.new(base64.b64decode(self._secret),
                path+chr(0) + urlencoded_data, hashlib.sha512)
        return base64.b64encode(str(mac.digest()))

    def _http_get(self, path):
        url = '{0}/{1}'.format(self._url, path)
        response = requests.get(url)
        if response.status_code != 200:
            raise MtgoxException({
                    'token': 'http_status_{0}'.format(response.status_code),
                    'error': response.text
                })
        return response.json()

    def _http_post(self, path, data={}):
        url = '{0}/{1}'.format(self._url, path)
        tonce = str(int(time.time()*1e6))
        payload = dict(data)
        payload['tonce'] = tonce
        headers = {
                'Rest-Key': self._key,
                'Rest-Sign': self._sign(path, payload),
            }
        response = requests.post(url, data=payload, headers=headers)
        if response.status_code != 200:
            raise MtgoxException({
                    'token': 'http_status_{0}'.format(response.status_code),
                    'error': response.text
                })
        return response.json()

    def _get_order_result(self, order):
        """ Get the order result of a closed order. Return None if the order
        can't be found.
        """
        path = 'BTCUSD/money/order/result'
        data = {
                'type': order.otype,
                'order': order.oid,
            }
        try:
            response = self._http_post(path, data)
        except MtgoxException as e:
            if e.token == 'unknown_order_id':
                return None
            else:
                raise
        if response['result'] != 'success':
            raise MtgoxException(response)
        data = response['data']
        return data

    def _get_order_lag(self):
        """ Get the order lag (in seconds).
        """
        path = 'BTCUSD/money/order/lag'
        response = self._http_post(path)
        if response['result'] != 'success':
            raise MtgoxException(response)
        lag = Decimal(response['data']['lag']) / Decimal('1e6')
        return lag
