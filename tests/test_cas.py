import tempfile
from pathlib import Path
import pytest
from rollback.cas import CASStore, DeduplicationReport


def test_cas_store_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cas_dir = Path(tmpdir) / "cas"
        cas = CASStore(cas_dir)

        # 1. Put and get bytes
        data1 = b"Hello, CAS world! This is a test string for content-addressed storage."
        hash1 = cas.put_bytes(data1)
        assert len(hash1) == 64
        assert cas.exists(hash1) is True

        retrieved1 = cas.get_bytes(hash1)
        assert retrieved1 == data1

        # 2. Idempotent put
        hash1_repeat = cas.put_bytes(data1)
        assert hash1_repeat == hash1

        # 3. Nonexistent object
        with pytest.raises(KeyError):
            cas.get_bytes("a" * 64)

        # 4. Put and get file
        file_to_store = Path(tmpdir) / "original.txt"
        file_to_store.write_bytes(b"File content for testing CAS put_file and get_file.")
        hash2 = cas.put_file(file_to_store)
        assert cas.exists(hash2) is True

        restored_file = Path(tmpdir) / "restored.txt"
        ok = cas.get_file(hash2, restored_file)
        assert ok is True
        assert restored_file.read_bytes() == file_to_store.read_bytes()

        # 5. Stats and deduplication report
        stats = cas.get_stats(sample_raw_estimate=2000)
        assert isinstance(stats, DeduplicationReport)
        assert stats.total_objects == 2
        assert stats.stored_bytes > 0
        assert stats.raw_bytes == 2000
        stats_dict = stats.to_dict()
        assert stats_dict["total_objects"] == 2
        assert "deduplication_ratio" in stats_dict

        # 6. Prune unreferenced objects
        pruned = cas.prune_unreferenced(referenced_hashes={hash1})
        assert pruned == 1
        assert cas.exists(hash1) is True
        assert cas.exists(hash2) is False


def test_cas_empty_and_large_blobs() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        cas = CASStore(Path(tmpdir) / "cas")

        # Empty bytes
        empty_hash = cas.put_bytes(b"")
        assert cas.exists(empty_hash) is True
        assert cas.get_bytes(empty_hash) == b""

        # Repetitive compressible data
        large_data = b"REPETITIVE_BLOCK_" * 10000  # ~170KB
        large_hash = cas.put_bytes(large_data)
        assert cas.get_bytes(large_hash) == large_data

        # Verify compression saved bytes
        stats = cas.get_stats(sample_raw_estimate=len(large_data))
        assert stats.stored_bytes < len(large_data)
        assert stats.savings_percent > 50.0
