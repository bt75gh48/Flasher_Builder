#define _FILE_OFFSET_BITS 64

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <zlib.h>

#define LOCAL_FILE_HEADER_MAGIC 0x04034b50U
#define CENTRAL_DIRECTORY_MAGIC 0x02014b50U
#define ZIP64_EXTRA_ID 0x0001U
#define STORED_METHOD 0U
#define DEFLATED_METHOD 8U
#define DATA_DESCRIPTOR_FLAG 0x0008U
#define ENCRYPTED_FLAG 0x0001U
#define FIXED_HEADER_SIZE 30U
#define BUFFER_SIZE (8U * 1024U * 1024U)
#define LOG_INTERVAL (1024ULL * 1024ULL * 1024ULL)

static uint16_t get_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static uint64_t get_le64(const uint8_t *p) {
    return (uint64_t)get_le32(p) | ((uint64_t)get_le32(p + 4) << 32);
}

static int read_exact(int fd, void *buffer, size_t size) {
    uint8_t *p = buffer;

    while (size != 0) {
        ssize_t result = read(fd, p, size);
        if (result == 0) {
            fprintf(stderr, "Unexpected end of ZIP file\n");
            return -1;
        }
        if (result < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "ZIP read failed: %s\n", strerror(errno));
            return -1;
        }
        p += (size_t)result;
        size -= (size_t)result;
    }
    return 0;
}

static int write_all(int fd, const void *buffer, size_t size) {
    const uint8_t *p = buffer;

    while (size != 0) {
        ssize_t result = write(fd, p, size);
        if (result < 0) {
            if (errno == EINTR) continue;
            fprintf(stderr, "Stream write failed: %s\n", strerror(errno));
            return -1;
        }
        if (result == 0) {
            fprintf(stderr, "Stream write returned zero bytes\n");
            return -1;
        }
        p += (size_t)result;
        size -= (size_t)result;
    }
    return 0;
}

static int stream_stored(int fd, uint64_t size, uint8_t *buffer,
                         uint64_t *written, uLong *crc) {
    while (size != 0) {
        size_t amount = size > BUFFER_SIZE ? BUFFER_SIZE : (size_t)size;
        if (read_exact(fd, buffer, amount) != 0 ||
            write_all(STDOUT_FILENO, buffer, amount) != 0) {
            return -1;
        }
        *crc = crc32(*crc, buffer, (uInt)amount);
        *written += amount;
        size -= amount;
    }
    return 0;
}

static int stream_deflated(int fd, uint64_t compressed_size,
                           uint64_t uncompressed_size, uint8_t *input,
                           uint8_t *output, uint64_t *written, uLong *crc) {
    z_stream stream;
    int finished = 0;
    int status;

    memset(&stream, 0, sizeof(stream));
    status = inflateInit2(&stream, -MAX_WBITS);
    if (status != Z_OK) {
        fprintf(stderr, "Unable to initialize Deflate decoder: %d\n", status);
        return -1;
    }

    while (compressed_size != 0 && !finished) {
        size_t amount = compressed_size > BUFFER_SIZE
                            ? BUFFER_SIZE
                            : (size_t)compressed_size;
        if (read_exact(fd, input, amount) != 0) {
            inflateEnd(&stream);
            return -1;
        }
        compressed_size -= amount;
        stream.next_in = input;
        stream.avail_in = (uInt)amount;

        while (stream.avail_in != 0) {
            size_t produced;
            stream.next_out = output;
            stream.avail_out = BUFFER_SIZE;
            status = inflate(&stream, Z_NO_FLUSH);
            produced = BUFFER_SIZE - stream.avail_out;

            if (produced != 0) {
                if (*written > uncompressed_size - produced ||
                    write_all(STDOUT_FILENO, output, produced) != 0) {
                    inflateEnd(&stream);
                    return -1;
                }
                *crc = crc32(*crc, output, (uInt)produced);
                *written += produced;
            }

            if (status == Z_STREAM_END) {
                finished = 1;
                if (stream.avail_in != 0 || compressed_size != 0) {
                    fprintf(stderr, "Trailing bytes after Deflate stream\n");
                    inflateEnd(&stream);
                    return -1;
                }
                break;
            }
            if (status != Z_OK) {
                fprintf(stderr, "Deflate decode failed: %d (%s)\n", status,
                        stream.msg == NULL ? "no detail" : stream.msg);
                inflateEnd(&stream);
                return -1;
            }
            if (produced == 0 && stream.avail_in == 0) break;
        }
    }

    inflateEnd(&stream);
    if (!finished) {
        fprintf(stderr, "Unexpected end of Deflate stream\n");
        return -1;
    }
    return 0;
}

static int resolve_sizes(uint32_t compressed32, uint32_t uncompressed32, const uint8_t *extra,
                         size_t extra_size, uint64_t *compressed, uint64_t *uncompressed) {
    size_t position = 0;

    *compressed = compressed32;
    *uncompressed = uncompressed32;
    if (compressed32 != UINT32_MAX && uncompressed32 != UINT32_MAX) return 0;

    while (position + 4 <= extra_size) {
        uint16_t id = get_le16(extra + position);
        uint16_t size = get_le16(extra + position + 2);
        const uint8_t *data;
        size_t remaining;

        position += 4;
        if ((size_t)size > extra_size - position) break;
        if (id != ZIP64_EXTRA_ID) {
            position += size;
            continue;
        }

        data = extra + position;
        remaining = size;
        if (uncompressed32 == UINT32_MAX) {
            if (remaining < 8) return -1;
            *uncompressed = get_le64(data);
            data += 8;
            remaining -= 8;
        }
        if (compressed32 == UINT32_MAX) {
            if (remaining < 8) return -1;
            *compressed = get_le64(data);
        }
        return 0;
    }

    return -1;
}

int main(int argc, char **argv) {
    uint8_t header[FIXED_HEADER_SIZE];
    uint8_t *name = NULL;
    uint8_t *extra = NULL;
    uint8_t *buffer = NULL;
    uint8_t *output_buffer = NULL;
    int zip_fd = -1;
    int result = 1;

    if (argc != 3) {
        fprintf(stderr, "Usage: %s ZIP_FILE STORED_ENTRY\n", argv[0]);
        return 2;
    }

    signal(SIGPIPE, SIG_IGN);
    fprintf(stderr, "ZIP streamer started: zip=%s entry=%s\n", argv[1], argv[2]);

    zip_fd = open(argv[1], O_RDONLY);
    if (zip_fd < 0) {
        fprintf(stderr, "Cannot open ZIP file: %s\n", strerror(errno));
        goto done;
    }

    for (;;) {
        uint32_t signature;
        uint16_t flags;
        uint16_t method;
        uint16_t name_size;
        uint16_t extra_size;
        uint32_t expected_crc;
        uint64_t compressed_size;
        uint64_t uncompressed_size;

        free(name);
        free(extra);
        name = NULL;
        extra = NULL;

        if (read_exact(zip_fd, header, sizeof(header)) != 0) goto done;
        signature = get_le32(header);
        if (signature == CENTRAL_DIRECTORY_MAGIC) {
            fprintf(stderr, "ZIP entry not found: %s\n", argv[2]);
            goto done;
        }
        if (signature != LOCAL_FILE_HEADER_MAGIC) {
            fprintf(stderr, "Unexpected ZIP signature: 0x%08x\n", signature);
            goto done;
        }

        flags = get_le16(header + 6);
        method = get_le16(header + 8);
        expected_crc = get_le32(header + 14);
        name_size = get_le16(header + 26);
        extra_size = get_le16(header + 28);

        if ((flags & (ENCRYPTED_FLAG | DATA_DESCRIPTOR_FLAG)) != 0) {
            fprintf(stderr, "Unsupported ZIP flags 0x%04x\n", flags);
            goto done;
        }

        name = malloc((size_t)name_size + 1);
        extra = malloc(extra_size == 0 ? 1 : extra_size);
        if (name == NULL || extra == NULL) {
            fprintf(stderr, "Unable to allocate ZIP header buffers\n");
            goto done;
        }
        if (read_exact(zip_fd, name, name_size) != 0 ||
            read_exact(zip_fd, extra, extra_size) != 0) {
            goto done;
        }
        name[name_size] = '\0';

        if (resolve_sizes(get_le32(header + 18), get_le32(header + 22), extra, extra_size,
                          &compressed_size, &uncompressed_size) != 0) {
            fprintf(stderr, "Invalid ZIP64 sizes for %s\n", name);
            goto done;
        }

        if (strcmp((const char *)name, argv[2]) != 0) {
            if (compressed_size > INT64_MAX ||
                lseek(zip_fd, (off_t)compressed_size, SEEK_CUR) < 0) {
                fprintf(stderr, "Cannot skip ZIP entry %s: %s\n", name, strerror(errno));
                goto done;
            }
            continue;
        }

        if (method != STORED_METHOD && method != DEFLATED_METHOD) {
            fprintf(stderr, "ZIP entry %s uses unsupported method %u\n", name, method);
            goto done;
        }
        if (method == STORED_METHOD && compressed_size != uncompressed_size) {
            fprintf(stderr, "Stored ZIP entry has inconsistent sizes\n");
            goto done;
        }

        fprintf(stderr,
                "Found ZIP entry: method=%s compressed=%" PRIu64
                " uncompressed=%" PRIu64 "\n",
                method == STORED_METHOD ? "stored" : "deflate",
                compressed_size, uncompressed_size);
        buffer = malloc(BUFFER_SIZE);
        output_buffer = malloc(BUFFER_SIZE);
        if (buffer == NULL || output_buffer == NULL) {
            fprintf(stderr, "Unable to allocate ZIP stream buffers\n");
            goto done;
        }

        {
            uint64_t streamed = 0;
            uLong crc = crc32(0L, Z_NULL, 0);
            int stream_status;

            if (method == STORED_METHOD) {
                stream_status = stream_stored(zip_fd, compressed_size, buffer,
                                              &streamed, &crc);
            } else {
                stream_status = stream_deflated(zip_fd, compressed_size,
                                                uncompressed_size, buffer,
                                                output_buffer, &streamed, &crc);
            }
            if (stream_status != 0) goto done;
            if (streamed != uncompressed_size) {
                fprintf(stderr,
                        "ZIP entry produced %" PRIu64 ", expected %" PRIu64
                        " bytes\n",
                        streamed, uncompressed_size);
                goto done;
            }
            if ((uint32_t)crc != expected_crc) {
                fprintf(stderr, "ZIP entry CRC mismatch: %08x != %08x\n",
                        (uint32_t)crc, expected_crc);
                goto done;
            }
        }

        fprintf(stderr, "ZIP entry stream complete\n");
        result = 0;
        break;
    }

done:
    if (zip_fd >= 0) close(zip_fd);
    free(buffer);
    free(output_buffer);
    free(extra);
    free(name);
    return result;
}
