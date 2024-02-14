import ibapi
from ibapi.client import EClient
from ibapi.common import BarData
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import *
import threading
import time
import ta  # Technical Analysis library for financial indicators
import numpy as np
import pandas as pd
import pytz  # Timezone library
import math
from datetime import datetime, timedelta
from dateutil import parser

# Initialize global variable for order IDs
orderId = 1

# Define a class for Interactive Brokers connection that inherits from EWrapper and EClient
class IBApi(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)  # Initialize connection client

    # Callback for receiving historical data
    def historicalData(self, reqId, bar):
        try:
            bot.on_bar_update(reqId, bar, False)  # Pass data to bot for processing
        except Exception as e:
            print(e)

    # Callback for receiving historical data updates
    def historicalDataUpdate(self, reqId, bar):
        try:
            bot.on_bar_update(reqId, bar, True)  # Pass real-time data to bot for processing
        except Exception as e:
            print(e)

    # Callback indicating the end of historical data transmission
    def historicalDataEnd(self, reqId, start, end):
        print(reqId)  # Log the request ID for debugging

    # Callback for receiving the next valid order ID
    def nextValidId(self, nextorderId):
        global orderId
        orderId = nextorderId  # Update global orderId with the next valid ID

    # Callback for receiving real-time bar data
    def realtimeBar(self, reqId, time, open_, high, low, close, volume, wap, count):
        super().realtimeBar(reqId, time, open_, high, low, close, volume, wap, count)
        try:
            # Pass real-time bar data to bot for processing
            bot.on_bar_update(reqId, time, open_, high, low, close, volume, wap, count)
        except Exception as e:
            print(e)

    # Error handling callback
    def error(self, id, errorCode, errorMsg, advancedOrderRejectJson='SS'):
        print(errorCode)  # Log error code
        print(errorMsg)  # Log error message

# Define a Bar class to represent market data
class Bar:
    def __init__(self):
        self.open = 0
        self.low = 0
        self.high = 0
        self.close = 0
        self.volume = 0
        self.date = ''

# Define the main Bot class for trading logic
class Bot:
    def __init__(self):
        self.ib = IBApi()  # Initialize IB API connection
        self.ib.connect("127.0.0.1", 7497, 124)  # Connect to TWS or IB Gateway
        ib_thread = threading.Thread(target=self.run_loop, daemon=True)  # Start IB API event loop in a separate thread
        ib_thread.start()
        time.sleep(1)  # Wait for the connection to establish

        self.symbol = input("Enter the symbol you want to trade: ")  # Prompt user for trading symbol
        self.barsize = int(input("Enter the barsize you want to trade in minutes: "))  # Prompt for bar size
        self.bars = []  # Initialize list to store bars
        self.currentBar = Bar()  # Initialize current bar
        self.smaPeriod = 50  # Set SMA period

        contract = Contract()  # Define a contract for the symbol
        contract.symbol = self.symbol.upper()
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        self.ib.reqIds(-1)  # Request the next valid order ID
        self.reqId = 1  # Initialize request ID for data requests
        # Calculate query time for historical data request
        queryTime = (datetime.now().astimezone(pytz.timezone("America/New_York")) - timedelta(days=1)).strftime("%Y%m%d %H:%M:%S")
        # Request historical data for the symbol
        self.ib.reqHistoricalData(self.reqId, contract, "", "2 D", f"{self.barsize} mins", "TRADES", 1, 1, True, [])
    
    # Method to keep the IB API event loop running
    def run_loop(self):
        self.ib.run()

    # Method to define a bracket order (main order with attached stop loss and take profit orders)
    def bracketOrder(self, parentOrderId, action, quantity, profitTarget, stopLoss):
        contract = Contract()  # Define contract for the order
        contract.symbol = self.symbol.upper()
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        # Define the main (parent) order
        parent = Order()
        parent.orderId = parentOrderId
        parent.orderType = "MKT"
        parent.action = action
        parent.totalQuantity = quantity
        parent.transmit = False  # Do not transmit immediately

        # Define the take profit order
        profitTargetOrder = Order()
        profitTargetOrder.orderId = parent.orderId + 1
        profitTargetOrder.orderType = "LMT"
        profitTargetOrder.action = "SELL" if action == "BUY" else "BUY"
        profitTargetOrder.totalQuantity = quantity
        profitTargetOrder.lmtPrice = round(profitTarget, 2)
        profitTargetOrder.transmit = False  # Do not transmit immediately

        # Define the stop loss order
        stopLossOrder = Order()
        stopLossOrder.orderId = parent.orderId + 2
        stopLossOrder.orderType = "STP"
        # Stop loss sells if original order was a buy, buys back if original was a sell
        stopLossOrder.action = "SELL" if action == "BUY" else "BUY"
        stopLossOrder.totalQuantity = quantity
        stopLossOrder.auxPrice = round(stopLoss, 2)  # Stop price
        stopLossOrder.transmit = True  # Transmit as the last order in the group

        bracketOrders = [parent, profitTargetOrder, stopLossOrder]
        return bracketOrders

    # Method to check if a new bar has started based on time elapsed
    def is_new_bar(self, bartime):
        if not self.currentBar.date:
            return True  # If no current bar, treat as new bar
        elapsed_time = (bartime - self.currentBar.date).total_seconds() / 60.0
        return elapsed_time >= self.barsize  # New bar if elapsed time exceeds bar size

    # Method to finalize the current bar and add it to the bars list
    def finalize_and_append_current_bar(self):
        if self.currentBar.date:
            self.bars.append(self.currentBar)
            self.currentBar = Bar()  # Reset current bar

    # Method to start tracking a new current bar with received bar data
    def start_new_current_bar(self, bar):
        self.currentBar = Bar()
        self.currentBar.open = bar.open
        self.currentBar.high = bar.high
        self.currentBar.low = bar.low
        self.currentBar.close = bar.close
        self.currentBar.volume = bar.volume
        self.currentBar.date = datetime.strptime(bar.date, "%Y%m%d %H:%M:%S US/Eastern").astimezone(pytz.timezone("America/New_York"))
        # Log the new bar's details
        print(f"the close is : {self.currentBar.close}")
        print(f"the high is : {self.currentBar.high}")
        print(f"the low is : {self.currentBar.low}")
        print(f"the open is : {self.currentBar.open}")
    
    # Method to calculate the current SMA based on close prices of the stored bars
    def calculate_sma(self):
        if len(self.bars) >= self.smaPeriod:  # Check if enough bars for SMA calculation
            close_prices = [bar.close for bar in self.bars[-self.smaPeriod:]]  # Extract close prices for SMA period
            close_prices_series = pd.Series(close_prices)
            sma_values = ta.trend.sma_indicator(close_prices_series, self.smaPeriod, True)  # Calculate SMA
            return sma_values.iloc[-1]  # Return the last SMA value
        return None  # Not enough data for SMA calculation

    # Method to calculate the SMA of the previous period
    def calculate_previous_sma(self):
        if len(self.bars) >= self.smaPeriod + 1:  # Check if enough bars for previous SMA calculation
            # Extract close prices for previous SMA period, excluding the last bar
            close_prices = [bar.close for bar in self.bars[-self.smaPeriod - 1:-1]]
            close_prices_series = pd.Series(close_prices)
            sma_values = ta.trend.sma_indicator(close_prices_series, self.smaPeriod, True)  # Calculate SMA
            return sma_values.iloc[-1]  # Return the last value, the SMA of the previous period
        return None  # Not enough data for previous SMA calculation
    
    # Method called on receiving new bar data to update bars and potentially execute trades
    def on_bar_update(self, reqId, bar, realtime):
        global orderId  # Access the global order ID
        if not realtime:
            self.bars.append(bar)  # Append historical bar data to the bars list
        else:
            bartime = datetime.strptime(bar.date, "%Y%m%d %H:%M:%S US/Eastern").astimezone(pytz.timezone("America/New_York"))
            if self.is_new_bar(bartime):
                self.finalize_and_append_current_bar()  # Finalize and store the current bar
                self.start_new_current_bar(bar)  # Start a new current bar with received data
                sma_value = self.calculate_sma()  # Calculate current SMA
                if sma_value is not None:  # If SMA calculation is possible
                    print(f"SMA: {sma_value}")  # Log SMA value
                    lastLow = self.bars[-1].low  # Last bar's low
                    lastHigh = self.bars[-1].high  # Last bar's high
                    lastClose = self.bars[-1].close  # Last bar's close
                    
                    current_sma = self.calculate_sma()  # Calculate current SMA
                    if current_sma is not None:
                        current_sma_str = str(current_sma)  # Convert current SMA to string for comparison
                        previous_sma_str = str(self.calculate_previous_sma())  # Calculate and convert previous SMA to string
                        
                        # Trading logic based on SMA values and bar data
                        if (bar.close > lastHigh and
                                self.currentBar.low > lastLow and
                                bar.close > current_sma_str and
                                lastClose < previous_sma_str):
                            print("HERE3")  # Log for debugging
                            profitTarget = bar.close * 1.02  # Set profit target
                            stopLoss = bar.close * 0.99  # Set stop loss
                            quantity = 1  # Set trade quantity
                            bracket = self.bracketOrder(orderId, "BUY", quantity, profitTarget, stopLoss)  # Create bracket order
                            for o in bracket:  # Iterate over orders in the bracket
                                print("HERE4")  # Log for debugging
                                o.ocaGroup = "OCA_" + str(orderId)  # Set OCA group for the bracket
                                o.ocaType = 2  # Set OCA type
                                self.ib.placeOrder(o.orderId, o.contract, o)  # Place each order in the bracket
                            orderId += 3  # Increment global order ID for the next set of orders
            else:
                # Update the current bar with the latest data
                self.currentBar.high = max(self.currentBar.high, bar.high)  # Update high
                self.currentBar.low = min(self.currentBar.low, bar.low)  # Update low
                self.currentBar.close = bar.close  # Update close

# Instantiate and start the bot
bot = Bot()
