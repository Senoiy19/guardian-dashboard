from decimal import Decimal as D
from rate_engine import MarketTrove, analyze_rate, debt_ahead

market = [
    MarketTrove(D('1000'), D('2'), 100),
    MarketTrove(D('2000'), D('3'), 200),
    MarketTrove(D('3000'), D('4'), 300),
    MarketTrove(D('4000'), D('5'), 400),
]
assert debt_ahead(market, D('4'), 250) == D('6000')  # lower rates + equal rate set later
r = analyze_rate(debt_bold=D('10000'), current_rate_pct=D('5'), last_rate_adj_time=500, market=market)
assert r['current']['annual_interest_bold'] == 500.0
assert r['options']['balanced']['rate_pct'] == 3.0
assert r['options']['balanced']['annual_saving_vs_current_bold'] == 200.0
print('rate engine tests passed')
