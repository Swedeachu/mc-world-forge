#include "mcwf/leveldb_log.hpp"

#include <algorithm>
#include <array>
#include <fstream>
#include <stdexcept>

namespace {

constexpr int LOG_BLOCK_SIZE = 32768;
constexpr int LOG_HEADER_SIZE = 7;
constexpr uint32_t LEVELDB_MASK_DELTA = 0xa282ead8u;

void putFixed32(std::vector<uint8_t>& out, uint32_t v) {
    out.push_back(static_cast<uint8_t>(v & 0xff));
    out.push_back(static_cast<uint8_t>((v >> 8) & 0xff));
    out.push_back(static_cast<uint8_t>((v >> 16) & 0xff));
    out.push_back(static_cast<uint8_t>((v >> 24) & 0xff));
}

void putFixed64(std::vector<uint8_t>& out, uint64_t v) {
    for (int i = 0; i < 8; ++i) {
        out.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xff));
    }
}

void putVarint32(std::vector<uint8_t>& out, uint32_t v) {
    while (v >= 0x80) {
        out.push_back(static_cast<uint8_t>(v | 0x80));
        v >>= 7;
    }
    out.push_back(static_cast<uint8_t>(v));
}

void putVarint64(std::vector<uint8_t>& out, uint64_t v) {
    while (v >= 0x80) {
        out.push_back(static_cast<uint8_t>(v | 0x80));
        v >>= 7;
    }
    out.push_back(static_cast<uint8_t>(v));
}

void putLengthPrefixedSlice(std::vector<uint8_t>& out, const std::string& s) {
    putVarint32(out, static_cast<uint32_t>(s.size()));
    out.insert(out.end(), s.begin(), s.end());
}

void putLengthPrefixedBytes(std::vector<uint8_t>& out, const std::vector<uint8_t>& bytes) {
    putVarint32(out, static_cast<uint32_t>(bytes.size()));
    out.insert(out.end(), bytes.begin(), bytes.end());
}

uint32_t crc32cExtend(uint32_t crc, const uint8_t* data, size_t len) {
    crc = ~crc;

    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];

        for (int k = 0; k < 8; ++k) {
            uint32_t mask = static_cast<uint32_t>(-static_cast<int32_t>(crc & 1u));
            crc = (crc >> 1) ^ (0x82f63b78u & mask);
        }
    }

    return ~crc;
}

uint32_t maskedCrc32c(uint8_t type, const uint8_t* data, size_t len) {
    uint32_t crc = crc32cExtend(0, &type, 1);
    crc = crc32cExtend(crc, data, len);
    return ((crc >> 15) | (crc << 17)) + LEVELDB_MASK_DELTA;
}

void writePhysicalRecord(std::ofstream& out, uint8_t type, const uint8_t* data, size_t len) {
    if (len > 0xffff) {
        throw std::runtime_error("LevelDB log record fragment is too large");
    }

    uint32_t crc = maskedCrc32c(type, data, len);
    uint8_t header[LOG_HEADER_SIZE] = {
        static_cast<uint8_t>(crc & 0xff),
        static_cast<uint8_t>((crc >> 8) & 0xff),
        static_cast<uint8_t>((crc >> 16) & 0xff),
        static_cast<uint8_t>((crc >> 24) & 0xff),
        static_cast<uint8_t>(len & 0xff),
        static_cast<uint8_t>((len >> 8) & 0xff),
        type
    };

    out.write(reinterpret_cast<const char*>(header), sizeof(header));
    if (len > 0) {
        out.write(reinterpret_cast<const char*>(data), static_cast<std::streamsize>(len));
    }
}

void writeLogRecord(std::ofstream& out, const std::vector<uint8_t>& record, int& blockOffset) {
    size_t offset = 0;
    bool begin = true;

    while (offset < record.size() || (begin && record.empty())) {
        int leftover = LOG_BLOCK_SIZE - blockOffset;
        if (leftover < LOG_HEADER_SIZE) {
            std::array<uint8_t, LOG_BLOCK_SIZE> zeros{};
            out.write(reinterpret_cast<const char*>(zeros.data()), leftover);
            blockOffset = 0;
            leftover = LOG_BLOCK_SIZE;
        }

        int avail = leftover - LOG_HEADER_SIZE;
        size_t fragment = std::min(static_cast<size_t>(avail), record.size() - offset);
        bool end = offset + fragment >= record.size();
        uint8_t type;

        if (begin && end) {
            type = 1; // FULL
        } else if (begin) {
            type = 2; // FIRST
        } else if (end) {
            type = 4; // LAST
        } else {
            type = 3; // MIDDLE
        }

        writePhysicalRecord(out, type, record.data() + offset, fragment);
        blockOffset += LOG_HEADER_SIZE + static_cast<int>(fragment);
        offset += fragment;
        begin = false;

        if (record.empty()) {
            break;
        }
    }
}

void writeManifestRecord(const fs::path& path, const std::vector<uint8_t>& edit) {
    std::ofstream out(path, std::ios::binary);
    if (!out) {
        throw std::runtime_error("could not write LevelDB MANIFEST");
    }
    int blockOffset = 0;
    writeLogRecord(out, edit, blockOffset);
}

std::vector<uint8_t> buildManifestEdit(uint64_t count) {
    std::vector<uint8_t> edit;
    putVarint32(edit, 1); // comparator
    putLengthPrefixedSlice(edit, "leveldb.BytewiseComparator");
    putVarint32(edit, 2); // log number
    putVarint64(edit, 3);
    putVarint32(edit, 3); // next file number
    putVarint64(edit, 4);
    putVarint32(edit, 4); // last sequence
    putVarint64(edit, count);
    return edit;
}

std::vector<uint8_t> buildWriteBatch(const std::vector<LevelDbLogEntry>& entries, size_t begin, size_t end, uint64_t sequence) {
    std::vector<uint8_t> batch;
    putFixed64(batch, sequence);
    putFixed32(batch, static_cast<uint32_t>(end - begin));

    for (size_t i = begin; i < end; ++i) {
        const LevelDbLogEntry& entry = entries[i];
        batch.push_back(1); // kTypeValue
        putLengthPrefixedSlice(batch, entry.key);
        putLengthPrefixedBytes(batch, entry.value);
    }

    return batch;
}

} // namespace

void writeLevelDbLogStore(const fs::path& dbDir, std::vector<LevelDbLogEntry>&& entries) {
    fs::remove_all(dbDir);
    fs::create_directories(dbDir);

    std::sort(entries.begin(), entries.end(), [](const LevelDbLogEntry& a, const LevelDbLogEntry& b) {
        return a.key < b.key;
    });

    writeManifestRecord(dbDir / "MANIFEST-000002", buildManifestEdit(static_cast<uint64_t>(entries.size())));

    {
        std::ofstream current(dbDir / "CURRENT", std::ios::binary);
        if (!current) {
            throw std::runtime_error("could not write LevelDB CURRENT");
        }
        current << "MANIFEST-000002\n";
    }

    {
        std::ofstream lock(dbDir / "LOCK", std::ios::binary);
    }

    {
        std::ofstream info(dbDir / "LOG", std::ios::binary);
        info << "mc-world-forge created this minimal log-backed LevelDB store.\n";
    }

    std::ofstream log(dbDir / "000003.log", std::ios::binary);
    if (!log) {
        throw std::runtime_error("could not write LevelDB log");
    }

    constexpr size_t MAX_BATCH_ENTRIES = 2048;
    constexpr size_t MAX_BATCH_BYTES = 4 * 1024 * 1024;

    uint64_t sequence = 1;
    size_t begin = 0;
    int blockOffset = 0;
    while (begin < entries.size()) {
        size_t end = begin;
        size_t approxBytes = 12;

        while (end < entries.size() && end - begin < MAX_BATCH_ENTRIES && approxBytes < MAX_BATCH_BYTES) {
            approxBytes += entries[end].key.size() + entries[end].value.size() + 16;
            ++end;
        }

        writeLogRecord(log, buildWriteBatch(entries, begin, end, sequence), blockOffset);
        sequence += static_cast<uint64_t>(end - begin);
        begin = end;
    }
}
