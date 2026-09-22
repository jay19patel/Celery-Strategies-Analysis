from app.core.celery_app import celery_app
from app.core.tasks import execute_strategy_task
import time
import pprint

# Trigger dummy heavy strategy
res = execute_strategy_task.delay("Dummy Heavy Strategy", "BTCUSD", 1, 1)
print(f"Task triggered: {res.id}")

time.sleep(2) # wait a moment for it to start

# Inspect active
active = celery_app.control.inspect().active()
pprint.pprint(active)
