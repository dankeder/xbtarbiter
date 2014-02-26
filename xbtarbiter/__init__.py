"""
XBT exchange arbiter.

Usage:
  xbtarbiter [--plugins=<plugins>] balance
  xbtarbiter [--plugins=<plugins>] prices
  xbtarbiter [--plugins=<plugins>] orders
  xbtarbiter [--plugins=<plugins>] trading [--dry-run] [--min-profit=<profit>] [--max-volume=<volume>] [--no-confirm]
  xbtarbiter (-h | --help)

Commands:
  balance   print account balance
  prices    print current highest-bid and lowest-ask prices
  orders    print open orders for each market
  trading   start interactive trading

Options:
  --dry-run               Dry-run operation
  --min-profit=<profit>   Min profit to make a trade (in USD) [default: 0.0]
  --max-volume=<volume>   Max volume to trade in one order (in XBT) [default: 0.01]
  --no-confirm            Trade automatically, do not confirm trades
  --plugins=<pluginlist>  Comma-separated list of plugins to enable [default: all]

Available Plugins:
  bitstamp
  kraken
"""
import os
import sys
import time
import gnupg
try:
    import simplejson as json
except ImportError:
    import json
from docopt import docopt
from decimal import Decimal
from datetime import datetime
from getpass import getpass

from bitstamp import BitstampPlugin, BitstampException, BitstampOrder
from kraken import KrakenPlugin, KrakenException
from forex import get_eurusd


# Path to the default config file
DEFAULT_CFG_FILE="~/.xbtarbiter/config.gpg"

# Path to the default config file
DEFAULT_LOG_FILE="~/.xbtarbiter/orders.log"


ORDER_OPEN = 'open'
ORDER_CLOSED = 'closed'

# Minimum trade volume the exchanges will allow (in XBT)
MIN_TRADE_VOLUME = Decimal('0.01')

def print_account_balance(plugins):
    total_xbt = 0
    total_usd = 0

    for plugin in plugins:
        print "{market:20}  {bal_xbt: >11.8f} XBT    {bal_usd: >10.5f} USD    [fee {fee:.3}%]".format(
                market=plugin.name,
                bal_xbt=plugin.balance_xbt,
                bal_usd=plugin.balance_usd,
                fee=plugin.trade_fee)
        total_xbt += plugin.balance_xbt
        total_usd += plugin.balance_usd

    print '-' * 80
    print ' ' * 23 + '{total_xbt: >10.8f} XBT    {total_usd: >10.5f} USD'.format(
            total_xbt=total_xbt, total_usd=total_usd)


def print_prices(plugins):
    for plugin in plugins:
        plugin.refresh_order_book()
        print "{market:20}  BID {bid_vol: >11.8f} @ {bid_price: <10.5f} USD    ASK {ask_vol: >11.8f} @ {ask_price: <10.5f} USD".format(
                market=plugin.name,
                bid_vol=plugin.highest_bid['volume'],
                bid_price=plugin.highest_bid['price'],
                ask_vol=plugin.lowest_ask['volume'],
                ask_price=plugin.lowest_ask['price'])


def print_open_orders(plugins):
    for plugin in plugins:
        plugin.refresh_orders()
        print "{market} orders:".format(market=plugin.name)
        if plugin.open_orders:
            for order in plugin.open_orders:
                print order
                # print "  {oid}: {otype}".format(oid=order.oid, otype=order.otype)
        else:
            print "  [none]"


def calc_opportunity(bid_plugin, ask_plugin, max_volume):
    """ Determine whether bid/ask order pair is profitable or not. Take
    transaction fees into account as well.
    """
    bid = bid_plugin.highest_bid
    bid_fee = bid_plugin.trade_fee
    ask = ask_plugin.lowest_ask
    ask_fee = ask_plugin.trade_fee

    # Calculate max. available volume on the markets, the max. possible profit
    # and corresponding fees
    mkt_volume = min(bid['volume'], ask['volume'])
    mkt_buy_total = ask['price'] * mkt_volume
    mkt_buy_fee = ask['price'] * (ask_fee / 100) * mkt_volume
    mkt_sell_total = bid['price'] * mkt_volume
    mkt_sell_fee = bid['price'] * (bid_fee / 100) * mkt_volume
    mkt_fees = mkt_sell_fee + mkt_buy_fee
    mkt_profit = mkt_sell_total - mkt_buy_total - mkt_fees

    # Calculate the affordable volume
    can_buy_volume = ask_plugin.avail_usd / (ask['price'] * (1 + (ask_fee / 100)))
    can_sell_volume = bid_plugin.avail_xbt / (1 + (bid_fee / 100))
    affordable_volume = min(can_buy_volume, can_sell_volume)

    # Calculate the volume we will eventually trade, the profit and fees
    volume = min(mkt_volume, affordable_volume, max_volume)
    buy_total = ask['price'] * volume
    buy_fee = ask['price'] * (ask_fee / 100) * volume
    sell_total = bid['price'] * volume
    sell_fee = bid['price'] * (bid_fee / 100) * volume
    fees = sell_fee + buy_fee
    profit = sell_total - buy_total - fees

    return {
            'bid_plugin': bid_plugin,
            'ask_plugin': ask_plugin,

            'mkt_volume': mkt_volume,
            'mkt_buy_total': mkt_sell_total,
            'mkt_buy_fee': mkt_sell_fee,
            'mkt_sell_total': mkt_sell_total,
            'mkt_sell_fee': mkt_sell_fee,
            'mkt_fees': mkt_fees,
            'mkt_profit': mkt_profit,

            'volume': volume,
            'buy_total': buy_total,
            'buy_fee': buy_fee,
            'sell_total': sell_total,
            'sell_fee': sell_fee,
            'fees': fees,
            'profit': profit,
        }


def find_opportunities(plugins, max_volume):
    """ Find profitable opportunities.
    """
    for plugin in plugins:
        plugin.refresh_order_book()

    result = []
    for bid_plugin in plugins:
        for ask_plugin in plugins:
            if bid_plugin.name != ask_plugin.name:
                opportunity = calc_opportunity(bid_plugin, ask_plugin, max_volume)
                if opportunity['mkt_profit'] > 0:
                    result.append(opportunity)
    return result


def get_best_opportunity(opportunities, min_profit):
    """ Find the best opportunity for trading
    """
    best = None
    for opportunity in opportunities:
        if opportunity['profit'] >= min_profit and \
                (best is None or opportunity['profit'] > best['profit']):
            best = opportunity
    return best


def trade(plugins, min_profit, max_volume, logfile, confirm=True, dry_run=False):
    """ Find a profitable opportunity and perform a trade.

    :param plugins: Plugins.
    :param min_profit: Min. profit that must exist before entering trade orders.
    :param max_volume: Max. volume in XBT to trade.
    :param logfile: File-like object for logging trade orders.
    :param confirm: Confirm trade manually.
    :param dry-run: Dry-run mode.
    """
    # Find profitable opportunities
    opportunities = find_opportunities(plugins, max_volume)
    if not opportunities:
        print "No profitable opportunities exist on the markets."
        print
        if confirm:
            print "Press ENTER to continue."
            sys.stdin.readline()
        return

    # Find the best opportunity
    opportunity = get_best_opportunity(opportunities, min_profit)
    if opportunity is None:
        print "No opportunities with profit greater than {0:.5f} USD were found".format(min_profit)
        print
        if confirm:
            print "Press ENTER to continue."
            sys.stdin.readline()
        return

    ask_plugin = opportunity['ask_plugin']
    ask = ask_plugin.lowest_ask
    bid_plugin = opportunity['bid_plugin']
    bid = bid_plugin.highest_bid

    # Print the best buying/selling offers
    print "{market:20}  ASK {volume: >11.8f} @ {price: <10.5f} USD".format(
            market=ask_plugin.name,
            volume=ask['volume'], price=ask['price'])
    print "{market:20}  BID {volume: >11.8f} @ {price: <10.5f} USD".format(
            market=bid_plugin.name,
            volume=bid['volume'], price=bid['price'])
    print "--"

    # Print what will be bought/sold
    if opportunity['volume'] >= MIN_TRADE_VOLUME:
        print "{market:20}  BUY  {volume: >11.8f} XBT for {buy_total: >10.5f} USD  [fee {buy_fee:.5} USD]".format(
                market=ask_plugin.name,
                volume=opportunity['volume'],
                buy_total=opportunity['buy_total'],
                buy_fee=opportunity['buy_fee'])
        print "{market:20}  SELL {volume: >11.8f} XBT for {sell_total: >10.5f} USD  [fee {sell_fee:.5} USD]".format(
                market=bid_plugin.name,
                volume=opportunity['volume'],
                sell_total=opportunity['sell_total'],
                sell_fee=opportunity['sell_fee'])
        print " " * 22 + "FEES                     {fees: >10.5f} USD".format(
                fees=opportunity['fees'])
        print " " * 20 + "PROFIT                     {profit: >10.5f} USD".format(
                profit=opportunity['profit'])
        print "--"

        # Confirm the trade by the user
        if confirm:
            sys.stdout.write("Do you want to proceed with the trade? [Y/n] ")
            answer = sys.stdin.readline().strip()
        if not confirm or answer in ('', 'y'):
            if not dry_run:
                # Send BUY order
                buy_order = ask_plugin.create_bid_order(
                        volume=opportunity['volume'],
                        price=ask['price'])
                print "{market:20}  BUY order {order_id}".format(
                        market=ask_plugin.name,
                        order_id=buy_order.oid)
                logfile.write("{ts}  {market} open BUY order {order_id}: VOLUME {volume:11.8f} XBT  PRICE {price:10.5f} USD\n".format(
                        ts=datetime.now().isoformat(' '),
                        market=ask_plugin.name,
                        order_id=buy_order.oid,
                        volume=opportunity['volume'],
                        price=ask['price']))

                # Send SELL order
                sell_order = bid_plugin.create_ask_order(
                        volume=opportunity['volume'],
                        price=bid['price'])
                print "{market:20}  SELL order '{order_id}'".format(
                        market=bid_plugin.name,
                        order_id=sell_order.oid)
                logfile.write("{ts}  {market} open SELL order {order_id}: VOLUME {volume:11.8f} XBT  PRICE {price:10.5f} USD\n".format(
                        ts=datetime.now().isoformat(' '),
                        market=bid_plugin.name,
                        order_id=sell_order.oid,
                        volume=opportunity['volume'],
                        price=bid['price']))

                # Wait until the orders are closed
                open_buy_orders = [buy_order]
                open_sell_orders = [sell_order]
                while open_buy_orders or open_sell_orders:
                    for buy_order in open_buy_orders:
                        status = ask_plugin.get_order_status(buy_order)
                        print "{market:20}  BUY order '{order_id}': {status}".format(
                                market=ask_plugin.name,
                                order_id=buy_order.oid,
                                status=status)
                        if status == ORDER_CLOSED:
                            logfile.write("{ts}  {market} close BUY order {order_id}\n".format(
                                    ts=datetime.now().isoformat(' '),
                                    market=ask_plugin.name,
                                    order_id=buy_order.oid))
                            open_buy_orders.remove(buy_order)

                    for sell_order in open_sell_orders:
                        status = bid_plugin.get_order_status(sell_order)
                        print "{market:20}  SELL order '{order_id}': {status}".format(
                                market=bid_plugin.name,
                                order_id=sell_order.oid,
                                status=status)
                        if status == ORDER_CLOSED:
                            logfile.write("{ts}  {market} close SELL order {order_id}\n".format(
                                    ts=datetime.now().isoformat(' '),
                                    market=bid_plugin.name,
                                    order_id=sell_order.oid))
                            open_sell_orders.remove(sell_order)
                    print
        else:
            print "Skipping."
            print
    elif opportunity['volume'] > Decimal('0.0'):
        print "Skipping, volume too low: {volume:11.8f}".format(
                volume=opportunity['volume'])
        print
        if confirm:
            print "Press ENTER to continue."
            sys.stdin.readline()
            print
    else:
        print "No trading possible, insufficient funds."
        print
        if confirm:
            print "Press ENTER to continue."
            sys.stdin.readline()
            print



def connect(enabled_plugins, cfg):
    plugins = []

    # Connect to Bitstamp
    if 'bitstamp' in enabled_plugins:
        try:
            print "Connecting to Bitstamp ..."
            bitstamp_cfg = cfg['plugins']['bitstamp.net']
            bitstamp = BitstampPlugin(client_id=bitstamp_cfg['client_id'],
                    key=bitstamp_cfg['key'],
                    secret=str(bitstamp_cfg['secret']))
            plugins.append(bitstamp)
        except BitstampException as e:
            print "Failed to connect: {0}".format(str(e))

    # Connect to Kraken
    if 'kraken' in enabled_plugins:
        try:
            print "Connecting to Kraken ..."
            kraken_cfg = cfg['plugins']['kraken.com']
            kraken = KrakenPlugin(key=kraken_cfg['key'],
                    secret=kraken_cfg['secret'],
                    eurusd_rate=get_eurusd())
            plugins.append(kraken)
        except KrakenException as e:
            print "Failed to connect: {0}".format(str(e))

    print

    return plugins


def read_config(path):
    gpg = gnupg.GPG()
    with open(os.path.expanduser(path), 'r') as cfgfile:
        data = cfgfile.read()
        passphrase = getpass("Enter passphrase for decrypting config file: ")
        while True:
            crypt = gpg.decrypt(data, passphrase=passphrase)
            if not crypt.ok:
                passphrase = getpass("Wrong passphrase, try again: ")
            else:
                cfg = json.loads(crypt.data)
                return cfg

def open_logfile(path):
    return open(os.path.expanduser(path), 'a')



def main():
    # TODO: allow opening --max-positions trades. After --max-positions is
    # reached wait until at least one position is closed.
    # TODO: use logging module to log into file. Or at least encapsulate
    # logging into functions.
    # TODO: Create btc-e.com plugin
    try:
        opts = docopt(__doc__)

        # Sanity checks
        if Decimal(opts['--min-profit']) < 0:
            raise ValueError("Value of --min-profit must be greater than or equal to 0")
        if Decimal(opts['--max-volume']) < MIN_TRADE_VOLUME:
            raise ValueError("Value of --max-volume must be greater than or equal to {0} XBT".format(
                    MIN_TRADE_VOLUME))
        if opts['--plugins'] == 'all':
            enabled_plugins = ['bitstamp', 'kraken']
        else:
            enabled_plugins = []
            for plugin in opts['--plugins'].split(','):
                if plugin in ('bitstamp', 'kraken'):
                    enabled_plugins.append(plugin)
                else:
                    raise ValueError("Unknown plugin: {0}".format(plugin))


        # Read config
        cfg = read_config(DEFAULT_CFG_FILE)

        # Connect to exchanges
        plugins = connect(enabled_plugins, cfg)

        if opts['balance']:
            # Print account information for each market
            print_account_balance(plugins)
        elif opts['prices']:
            # Print highest bid & lowest ask for each market
            print_prices(plugins)
        elif opts['orders']:
            # Print open orders for each market
            print_open_orders(plugins)
        elif opts['trading']:
            # Interactive trading
            dry_run = opts['--dry-run']
            no_confirm = opts['--no-confirm']
            min_profit = Decimal(opts['--min-profit'])
            max_volume = Decimal(opts['--max-volume'])
            logfile = open_logfile(DEFAULT_LOG_FILE)

            if dry_run:
                print "=" * 80
                print "DRY RUN trading (no real trades will be performed)"
                print "=" * 80
                print

            if no_confirm:
                print "=" * 80
                print "AUTOMATIC TRADING - no trade confirmations"
                print "=" * 80
                print

            print "-" * 80
            print "Max volume is {0} XBT".format(max_volume)
            print "-" * 80
            print

            ntrade = 1
            while True:
                try:
                    print "{ts} #{ntrade}".format(
                            ts=datetime.now().isoformat(' '),
                            ntrade=ntrade)
                    print "--"
                    ntrade += 1
                    trade(plugins=plugins,
                            min_profit=min_profit,
                            max_volume=max_volume,
                            logfile=logfile,
                            confirm=not no_confirm,
                            dry_run=dry_run)

                    if no_confirm:
                        time.sleep(10)

                except MtgoxException as e:
                    print "Mtgox.com: {0}".format(e)
                    print
                except BitstampException as e:
                    print "Bitstamp.net: {0}".format(e)
                    print
                except KrakenException as e:
                    print "Kraken.com: {0}".format(e)
                    print

    except KeyboardInterrupt:
        pass
    except MtgoxException as e:
        print "Mtgox.com: {0}".format(e)
    except BitstampException as e:
        print "Bitstamp.net: {0}".format(e)
    except KrakenException as e:
        print "Kraken.com: {0}".format(e)
    except ValueError as e:
        print e
