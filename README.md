xbtarbiter
==========

Xbtarbiter is a script for performing arbitrage between Bitcoin online
exchanges. Currently supported exchanges are:

  * bitstamp.net
  * kraken.com
  * mtgox.com


Installation
------------

Installing into a prepared python virtualenv:

    python setup.py install


Configuration
-------------

Configuration is stored in `~/.xbtarbiter/config.gpg`. It's a JSON file encrypted using
`gpg`.

Config file format:

    {
        "plugins": {
            "bitstamp.net": {
                "client_id": "<client_id>",
                "key": "<key>",
                "secret": "<secret>"
            },
            "mtgox.com": {
                "key": "<key>",
                "secret": "<secret>"
            },
            "kraken.com": {
                "key": "<key>",
                "secret": "<secret>"
            }
        }
    }

Note that these are NOT your login credentials, but API credentials.

How to encrypt it (you need to have a GPG keypair):

    gpg -r 'Your Name' -o config.gpg -e config.json

How to decrypt it:

    gpg -o config.json -d config.gpg


How to use
----------

Show your account balance:

    xbtarbiter balance

Show the current high/low prices:

    xbtarbiter prices

Show your currently open orders:

    xbtarbiter orders

Start trading:

    xbtarbiter trading

Show help:

    xbtarbiter -h


Licence
-------

MIT Licence


Author
------

Dan Keder <dan.keder@gmail.com>
