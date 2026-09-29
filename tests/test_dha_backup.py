import base64, datetime, importlib.util, os, pathlib, sqlite3, sys, tempfile, types
sys.path.insert(0, "/home/claude/shim")
import pytest

A = pathlib.Path(__file__).resolve().parents[1] / "app"
def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, A / rel); m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m; spec.loader.exec_module(m); return m
crypto = load("security/crypto.py", "app.security.crypto")
pkg = types.ModuleType("app"); sec = types.ModuleType("app.security"); sec.crypto = crypto
sys.modules.setdefault("app", pkg); sys.modules["app.security"] = sec
engine = load("backup/engine.py", "_backup_engine")
RING = crypto.build_keyring("b1:" + base64.urlsafe_b64encode(os.urandom(32)).decode(), "s")


def _db(path, rows=5):
    c = sqlite3.connect(path); c.execute("create table patients(id integer primary key, name text)")
    c.executemany("insert into patients(name) values (?)", [(f"p{i}",) for i in range(rows)]); c.commit(); c.close()


def test_backup_verify_restore_prune():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "h.db"); _db(db)
        res = engine.run_backup("sqlite:///" + db, os.path.join(d, "bk"), 35, RING)
        assert res["table_counts"] == {"patients": 5} and res["file_name"].endswith(".db.enc")
        with open(res["file"], "rb") as f:
            assert b"p1" not in f.read()                            # ciphertext, not plain
        ok, msg = engine.verify_backup(res["file"], res["sha256"], res["table_counts"], RING)
        assert ok, msg
        out = engine.restore_to(res["file"], os.path.join(d, "restored.db"), RING)
        assert sqlite3.connect(out).execute("select count(*) from patients").fetchone()[0] == 5
        with pytest.raises(engine.BackupError):
            engine.restore_to(res["file"], out, RING)               # refuses to overwrite
        assert engine.latest_backup_age_hours(os.path.join(d, "bk")) < 1


def test_verify_detects_tamper_wrong_key_and_count_drift():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "h.db"); _db(db)
        res = engine.run_backup("sqlite:///" + db, os.path.join(d, "bk"), 35, RING)
        assert not engine.verify_backup(res["file"], "0" * 64, None, RING)[0]
        other = crypto.build_keyring("b1:" + base64.urlsafe_b64encode(os.urandom(32)).decode(), "s")
        assert "Decryption failed" in engine.verify_backup(res["file"], res["sha256"], None, other)[1]
        assert not engine.verify_backup(res["file"], res["sha256"], {"patients": 6}, RING)[0]
        data = bytearray(open(res["file"], "rb").read()); data[60] ^= 1
        bad = os.path.join(d, "bad.enc"); open(bad, "wb").write(bytes(data))
        assert not engine.verify_backup(bad, None, None, RING)[0]


def test_prune_and_offsite():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "h.db"); _db(db)
        bk = os.path.join(d, "bk")
        res = engine.run_backup("sqlite:///" + db, bk, 35, RING)
        old = os.path.join(bk, "medicore-19990101-000000.db.enc"); open(old, "wb").write(b"x")
        os.utime(old, (0, 0))
        assert engine.prune(bk, 35) == 1 and not os.path.exists(old) and os.path.exists(res["file"])
        msg = engine.copy_offsite(res["file"], os.path.join(d, "off"))
        assert "copied to" in msg and os.path.exists(os.path.join(d, "off", res["file_name"]))
        assert engine.copy_offsite(res["file"]) == "not configured"


def test_non_sqlite_paths():
    with pytest.raises(engine.BackupError):
        engine.sqlite_path_from_uri("sqlite:///:memory:")
    assert engine.sqlite_path_from_uri("sqlite:////abs/x.db") == "/abs/x.db"
