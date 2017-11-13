from collections import deque
from numbers import Number

import numpy as np
import pandas as pd


class Aggregation(object):
    def update(self, acc, new_old):
        new, old = new_old
        if old is not None:
            acc, result = self.on_old(acc, old)
        if new is not None:
            acc, result = self.on_new(acc, new)
        return acc, result

    def stateless(self, new):
        acc = self.initial(new)
        acc, result = self.on_new(acc, new)
        return result


class Sum(Aggregation):
    def on_new(self, acc, new):
        if len(new):
            result = acc + new.sum()
        else:
            result = acc
        return result, result

    def on_old(self, acc, old):
        result = acc - old.sum()
        return result, result

    def initial(self, new):
        result = new.sum()
        if isinstance(result, Number):
            result = 0
        else:
            result[:] = 0
        return result


class Mean(Aggregation):
    def on_new(self, acc, new):
        totals, counts = acc
        if len(new):
            totals = totals + new.sum()
            counts = counts + new.count()
        return (totals, counts), totals / counts

    def on_old(self, acc, old):
        totals, counts = acc
        if len(old):
            totals = totals - old.sum()
            counts = counts - old.count()
        return (totals, counts), totals / counts

    def initial(self, new):
        s, c = new.sum(), new.count()
        if isinstance(s, Number):
            s = 0
            c = 0
        else:
            s[:] = 0
            c[:] = 0
        return (s, c)


class Count(Aggregation):
    def on_new(self, acc, new):
        result = acc + new.count()
        return result, result

    def on_old(self, acc, old):
        result = acc - old.count()
        return result, result

    def initial(self, new):
        return new.iloc[:0].count()


class Var(Aggregation):
    def __init__(self, ddof=1):
        self.ddof = ddof

    def _compute_result(self, x, x2, n):
        result = (x2 / n) - (x / n) ** 2
        if self.ddof != 0:
            result = result * n / (n - self.ddof)
        return result

    def on_new(self, acc, new):
        x, x2, n = acc
        if len(new):
            x = x + new.sum()
            x2 = x2 + (new ** 2).sum()
            n = n + new.count()

        return (x, x2, n), self._compute_result(x, x2, n)

    def on_old(self, acc, new):
        x, x2, n = acc
        if len(new):
            x = x - new.sum()
            x2 = x2 - (new ** 2).sum()
            n = n - new.count()

        return (x, x2, n), self._compute_result(x, x2, n)

    def initial(self, new):
        s = new.sum()
        c = new.count()
        if isinstance(s, Number):
            s = 0
            c = 0
        else:
            s[:] = 0
            c[:] = 0
        return (s, s, c)


class Full(Aggregation):
    def on_new(self, acc, new):
        result = pd.concat([acc, new])
        return result, result

    def on_old(self, acc, old):
        result = acc.iloc[len(old):]
        return result, result

    def stateless(self, new):
        return new


def diff_iloc(dfs, new, window=None):
    dfs = deque(dfs)
    dfs.append(new)
    old = []
    n = sum(map(len, dfs)) - window
    while n > 0:
        if len(dfs[0]) <= n:
            df = dfs.popleft()
            old.append(df)
            n -= len(df)
        else:
            old.append(dfs[0].iloc[:n])
            dfs[0] = dfs[0].iloc[n:]
            n = 0

    return dfs, old


def diff_loc(dfs, new, window=None):
    dfs = deque(dfs)
    dfs.append(new)
    mx = max(df.index.max() for df in dfs)
    mn = mx - window
    old = []
    while dfs[0].index.min() < mn:
        o = dfs[0].loc[:mn]
        old.append(o)  # TODO: avoid copy if fully lost
        dfs[0] = dfs[0].iloc[len(o):]
        if not len(dfs[0]):
            dfs.popleft()

    return dfs, old


def window_accumulator(acc, new, diff=None, window=None, agg=None):
    if acc is None:
        acc = {'dfs': [], 'state': agg.initial(new)}
    dfs = acc['dfs']
    state = acc['state']
    dfs, old = diff(dfs, new, window=window)
    if new is not None:
        state, result = agg.on_new(state, new)
    for o in old:
        if len(o):
            state, result = agg.on_old(state, o)
    acc2 = {'dfs': dfs, 'state': state}
    return acc2, result


def accumulator(acc, new, agg=None):
    if acc is None:
        acc = agg.initial(new)
    return agg.on_new(acc, new)


class GroupbyAggregation(Aggregation):
    def __init__(self, columns, grouper=None, **kwargs):
        self.grouper = grouper
        self.columns = columns
        for k, v in kwargs.items():
            setattr(self, k, v)

    def grouped(self, df, grouper=None):
        if grouper is None:
            grouper = self.grouper

        g = df.groupby(grouper)

        if self.columns is not None:
            g = g[self.columns]

        return g


class GroupbySum(GroupbyAggregation):
    def on_new(self, acc, new, grouper=None):
        g = self.grouped(new, grouper=grouper)
        result = acc.add(g.sum(), fill_value=0)
        return result, result

    def on_old(self, acc, old, grouper=None):
        g = self.grouped(old)
        result = acc.sub(g.sum(), fill_value=0)
        return result, result

    def initial(self, new, grouper=None):
        if hasattr(grouper, 'iloc'):
            grouper = grouper.iloc[:0]
        if isinstance(grouper, (pd.Index, np.ndarray)):
            grouper = grouper[:0]
        return self.grouped(new.iloc[:0], grouper=grouper).sum()


class GroupbyCount(GroupbyAggregation):
    def on_new(self, acc, new, grouper=None):
        g = self.grouped(new, grouper=grouper)
        result = acc.add(g.count(), fill_value=0)
        result = result.astype(int)
        return result, result

    def on_old(self, acc, old, grouper=None):
        g = self.grouped(old)
        result = acc.sub(g.count(), fill_value=0)
        result = result.astype(int)
        return result, result

    def initial(self, new, grouper=None):
        if hasattr(grouper, 'iloc'):
            grouper = grouper.iloc[:0]
        if isinstance(grouper, (pd.Index, np.ndarray)):
            grouper = grouper[:0]
        return self.grouped(new.iloc[:0], grouper=grouper).count()


class GroupbyMean(GroupbyAggregation):
    def on_new(self, acc, new, grouper=None):
        totals, counts = acc
        g = self.grouped(new, grouper=grouper)
        totals = totals.add(g.sum(), fill_value=0)
        counts = counts.add(g.count(), fill_value=0)

        return (totals, counts), totals / counts

    def on_old(self, acc, old, grouper=None):
        totals, counts = acc
        g = self.grouped(old, grouper=grouper)
        totals = totals.sub(g.sum(), fill_value=0)
        counts = counts.sub(g.count(), fill_value=0)

        return (totals, counts), totals / counts

    def initial(self, new, grouper=None):
        if hasattr(grouper, 'iloc'):
            grouper = grouper.iloc[:0]
        if isinstance(grouper, (pd.Index, np.ndarray)):
            grouper = grouper[:0]
        g = self.grouped(new.iloc[:0], grouper=grouper)
        return (g.sum(), g.count())


class GroupbyVar(GroupbyAggregation):
    def _compute_result(self, x, x2, n):
        result = (x2 / n) - (x / n) ** 2
        if self.ddof != 0:
            result = result * n / (n - self.ddof)
        return result

    def on_new(self, acc, new, grouper=None):
        x, x2, n = acc
        g = self.grouped(new, grouper=grouper)
        if len(new):
            x = x.add(g.sum(), fill_value=0)
            x2 = x2.add(g.agg(lambda x: (x**2).sum()), fill_value=0)
            n = n.add(g.count(), fill_value=0)

        return (x, x2, n), self._compute_result(x, x2, n)

    def on_old(self, acc, old, grouper=None):
        x, x2, n = acc
        g = self.grouped(old, grouper=grouper)
        if len(old):
            x = x.sub(g.sum(), fill_value=0)
            x2 = x2.sub(g.agg(lambda x: (x**2).sum()), fill_value=0)
            n = n.sub(g.count(), fill_value=0)

        return (x, x2, n), self._compute_result(x, x2, n)

    def initial(self, new, grouper=None):
        if hasattr(grouper, 'iloc'):
            grouper = grouper.iloc[:0]
        if isinstance(grouper, (pd.Index, np.ndarray)):
            grouper = grouper[:0]

        new = new.iloc[:0]
        g = self.grouped(new, grouper=grouper)
        x = g.sum()
        x2 = g.agg(lambda x: (x**2).sum())
        n = g.count()

        return (x, x2, n)


def groupby_accumulator(acc, new, agg=None):
    if agg.grouper is None and isinstance(new, tuple):
        new, grouper = new
    else:
        grouper = None
    if acc is None:
        acc = agg.initial(new, grouper=grouper)
    result = agg.on_new(acc, new, grouper=grouper)
    return result
