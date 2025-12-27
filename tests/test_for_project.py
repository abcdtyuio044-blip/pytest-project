import pytest
import time
# Some sleep functions that gets a parameterized fixture

@pytest.fixture(params=["quick", "slow", "group3"])
def sleep_time(request):
    return request.param

def test_sleep(sleep_time):
    print("???????????????????????")
    time.sleep(1)
    print(f"Finished sleeping for {sleep_time}")
    assert True