import time
import os
from datetime import datetime
from pymongo import MongoClient
from rich.console import Console
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
from rich.live import Live
from rich.align import Align
from rich.text import Text
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URL)
db = client.stockanalysis
accounts_coll = db.broker_accounts
trades_coll = db.broker_trades

console = Console()

def generate_dashboard():
    # Fetch all accounts
    accounts = list(accounts_coll.find().sort("capital", -1))
    
    # Calculate totals
    total_capital = sum([acc["capital"] for acc in accounts]) if accounts else 0.0
    total_trades = sum([acc["total_trades"] for acc in accounts]) if accounts else 0
    total_wins = sum([acc["winning_trades"] for acc in accounts]) if accounts else 0
    total_win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    
    # Generate Table
    table = Table(title="📈 Paper Trading Broker - Strategy Portfolio Monitor", show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Rank", justify="center", width=4)
    table.add_column("Strategy Name", min_width=35)
    table.add_column("Capital ($)", justify="right", style="green")
    table.add_column("Return %", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Win %", justify="right")
    table.add_column("Position", justify="center")

    for i, acc in enumerate(accounts, 1):
        ret_pct = ((acc["capital"] - 100.0) / 100.0) * 100
        ret_str = f"{ret_pct:+.2f}%"
        ret_color = "green" if ret_pct >= 0 else "red"
        
        pos = acc.get("open_position")
        if pos:
            pos_color = "green" if pos["type"] == "LONG" else "red"
            pos_str = f"[{pos_color}]{pos['type']} {pos['symbol']}[/{pos_color}]"
        else:
            pos_str = "[dim]FLAT[/dim]"
            
        table.add_row(
            str(i),
            acc["_id"],
            f"${acc['capital']:.2f}",
            f"[{ret_color}]{ret_str}[/{ret_color}]",
            str(acc["total_trades"]),
            f"{acc['win_rate']:.1f}%",
            pos_str
        )
        
    # Generate summary panel
    summary_text = Text()
    summary_text.append(f"Total Portfolio Value: ", style="bold")
    summary_text.append(f"${total_capital:.2f}\n", style="bold green")
    summary_text.append(f"Active Strategies: ", style="bold")
    summary_text.append(f"{len(accounts)}\n")
    summary_text.append(f"Total System Trades: ", style="bold")
    summary_text.append(f"{total_trades}\n")
    summary_text.append(f"Global Win Rate: ", style="bold")
    summary_text.append(f"{total_win_rate:.1f}%\n")
    summary_text.append(f"Last Updated: ", style="bold dim")
    summary_text.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", style="dim")
    
    summary_panel = Panel(Align.center(summary_text), title="Overall System Performance", border_style="cyan")
    
    # Layout
    layout = Layout()
    layout.split_column(
        Layout(summary_panel, size=8),
        Layout(table)
    )
    return layout

if __name__ == "__main__":
    with Live(generate_dashboard(), refresh_per_second=1, screen=True) as live:
        try:
            while True:
                time.sleep(2)
                live.update(generate_dashboard())
        except KeyboardInterrupt:
            pass
