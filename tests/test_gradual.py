from chaos.gradual import RampLoad


def test_rampload_qps_starts_low_and_is_bounded():
    r = RampLoad("127.0.0.1:1", "/x", q0=10, q1=100, duration_s=30, threads=1)
    try:
        q = r.qps_now()
        assert 10 <= q <= 100
        assert abs(q - 10) < 5          # starts near q0
    finally:
        r.terminate()
