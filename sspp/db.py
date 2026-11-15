"""Thin Oracle data-access layer built on python-oracledb (thin mode, no client install needed)."""
import oracledb

oracledb.defaults.fetch_lobs = False  # return CLOBs as str

_pool = None


def init_pool(cfg):
    global _pool
    if _pool is None:
        _pool = oracledb.create_pool(
            user=cfg["DB_USER"], password=cfg["DB_PASSWORD"],
            dsn=f'{cfg["DB_HOST"]}:{cfg["DB_PORT"]}/{cfg["DB_SERVICE"]}',
            min=cfg["DB_POOL_MIN"], max=cfg["DB_POOL_MAX"], increment=1,
        )
    return _pool


def connect():
    return _pool.acquire()


def _rows(cur):
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def query(sql, params=None):
    with connect() as con:
        cur = con.cursor()
        cur.execute(sql, params or {})
        return _rows(cur)


def one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql, params=None, default=0):
    with connect() as con:
        cur = con.cursor()
        cur.execute(sql, params or {})
        r = cur.fetchone()
        return r[0] if r and r[0] is not None else default


def execute(sql, params=None):
    with connect() as con:
        cur = con.cursor()
        cur.execute(sql, params or {})
        con.commit()
        return cur.rowcount


def insert(sql, params=None):
    """Run an INSERT that ends with `RETURNING id INTO :new_id` and return the new id."""
    with connect() as con:
        cur = con.cursor()
        new_id = cur.var(oracledb.NUMBER)
        p = dict(params or {})
        p["new_id"] = new_id
        cur.execute(sql, p)
        con.commit()
        v = new_id.getvalue()
        return int(v[0] if isinstance(v, list) else v)


def executemany(sql, rows):
    with connect() as con:
        cur = con.cursor()
        cur.executemany(sql, rows)
        con.commit()
