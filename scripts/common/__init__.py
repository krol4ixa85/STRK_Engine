# Общие модули движка. Импортируются как `from common.X import ...`
# из скриптов в scripts/, которые запускаются как `python3 scripts/X.py`
# — тогда sys.path[0] == "scripts" и пакет виден.