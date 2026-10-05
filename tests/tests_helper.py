import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location('tw', Path(__file__).parent / 'test_workflow.py')
_tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tw)
KeyedTransport, RoleGateway = _tw.KeyedTransport, _tw.RoleGateway
_models, _policy, _workers = _tw._models, _tw._policy, _tw._workers
