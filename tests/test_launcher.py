from app.launcher import _browser_host_for_bind_host, build_parser


def test_browser_host_for_bind_host_maps_wildcard_hosts():
    assert _browser_host_for_bind_host("0.0.0.0") == "127.0.0.1"
    assert _browser_host_for_bind_host("::") == "127.0.0.1"
    assert _browser_host_for_bind_host("[::]") == "127.0.0.1"
    assert _browser_host_for_bind_host("127.0.0.1") == "127.0.0.1"


def test_launcher_parser_open_flag_and_port():
    parser = build_parser()
    args = parser.parse_args(["--open", "--port", "9001", "--host", "127.0.0.1"])
    assert args.open is True
    assert args.port == 9001
    assert args.host == "127.0.0.1"
