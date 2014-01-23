


def test_skip_line__null():
    dcp = DiffContextParser("")
    assert dcp.should_skip_line("--- /dev/null")


def test_skip_line__new_file():
    dcp = DiffContextParser("")
    assert dcp.should_skip_line("new file mode 100644")



def test_skip_line__index_no_permissions():
    dcp = DiffContextParser("")
    assert dcp.should_skip_line("index 0000000..78ce7f6")






    valid_positions = set([3, 9, 10, 11, 12])



