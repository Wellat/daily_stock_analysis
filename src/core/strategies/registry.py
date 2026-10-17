from .low_premium import LowPremiumStrategy

_STRATEGIES = {"low-premium": LowPremiumStrategy}

class DoubleLowStrategy(LowPremiumStrategy):
    strategy_id = "double-low"
    name = "双低轮动"
    description = "按价格与转股溢价率之和（双低值）升序轮动持有最优可转债。"
    version = "legacy-v1"
    live_mode = "order"

_STRATEGIES["double-low"] = DoubleLowStrategy

def register(strategy_cls):
    _STRATEGIES[strategy_cls.strategy_id] = strategy_cls
    return strategy_cls

def get_strategy(strategy_id):
    try: return _STRATEGIES[strategy_id]()
    except KeyError: raise ValueError(f"unsupported strategy_id: {strategy_id}")

def list_builtin_strategies():
    out=[]
    for cls in _STRATEGIES.values():
        out.append({"strategy_id":cls.strategy_id,"strategy_version":cls.version,"name":getattr(cls,"name",cls.strategy_id),"instrument_types":list(cls.instrument_types),"markets":list(cls.markets),"live_mode":getattr(cls,"live_mode",None),"description":getattr(cls,"description",cls.__doc__ or ""),"parameters":[dict(x) for x in cls.parameter_definitions]})
    return out
