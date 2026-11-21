import time

from garden.upload import upload
from tests.test_upload import Flaky

start = time.perf_counter()
for _ in range(1000):
    upload(Flaky(), [b"x" * 1024] * 8)
print(f"{time.perf_counter() - start:.3f}s for 1000 uploads")
