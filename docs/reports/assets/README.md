# Report Image Assets

Put all screenshots used by the reports in this folder.

Required filenames:

| File | Capture from |
|---|---|
| `race-before-jmeter.png` | JMeter summary/results before the race-condition fix |
| `race-after-jmeter.png` | JMeter summary/results after the race-condition fix |
| `resource-low-workers-jmeter.png` | JMeter results with low worker/thread settings |
| `resource-balanced-workers-jmeter.png` | JMeter results with balanced worker/thread settings |
| `resource-monitoring-before.png` | CPU/RAM/thread/connection monitoring during low-capacity run |
| `resource-monitoring-after.png` | CPU/RAM/thread/connection monitoring during balanced run |
| `async-checkout-before.png` | Timing before moving invoice/email work to queue |
| `async-checkout-after.png` | Timing after Celery queue dispatch |
| `async-flower-retry.png` | Flower screenshot showing task retry/failure handling |
| `nfr9-100-users-locust.png` | Locust 100-user full-system run |

Do not fake screenshots. If a run cannot be completed, leave the image
out and write the reason in the corresponding report.

