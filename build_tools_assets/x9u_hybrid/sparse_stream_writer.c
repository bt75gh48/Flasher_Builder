#define _FILE_OFFSET_BITS 64

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SPARSE_HEADER_MAGIC 0xed26ff3aU
#define CHUNK_TYPE_RAW 0xcac1U
#define CHUNK_TYPE_FILL 0xcac2U
#define CHUNK_TYPE_DONT_CARE 0xcac3U
#define CHUNK_TYPE_CRC32 0xcac4U
#define SPARSE_HEADER_LEN 28U
#define CHUNK_HEADER_LEN 12U
#define BUFFER_SIZE (8U * 1024U * 1024U)
#define PROGRESS_STEP_PERCENT 1U

struct progress_reporter {
    int ui_fd;
    uint64_t total_bytes;
    uint64_t produced;
    uint32_t next_percent;
};

static uint16_t get_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static int read_exact(int fd, void *buffer, size_t size) {
    uint8_t *p = buffer;

    while (size != 0) {
        ssize_t n = read(fd, p, size);
        if (n == 0) {
            fprintf(stderr, "Unexpected end of sparse stream\n");
            return -1;
        }
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "Read failed: %s\n", strerror(errno));
            return -1;
        }
        p += (size_t)n;
        size -= (size_t)n;
    }
    return 0;
}

static int discard_exact(int fd, uint64_t size, uint8_t *buffer) {
    while (size != 0) {
        size_t amount = size > BUFFER_SIZE ? BUFFER_SIZE : (size_t)size;
        if (read_exact(fd, buffer, amount) != 0) {
            return -1;
        }
        size -= amount;
    }
    return 0;
}

static int write_all(int fd, const void *buffer, size_t size) {
    const uint8_t *p = buffer;

    while (size != 0) {
        ssize_t n = write(fd, p, size);
        if (n < 0) {
            if (errno == EINTR) {
                continue;
            }
            fprintf(stderr, "Write failed: %s\n", strerror(errno));
            return -1;
        }
        if (n == 0) {
            fprintf(stderr, "Write returned zero bytes\n");
            return -1;
        }
        p += (size_t)n;
        size -= (size_t)n;
    }
    return 0;
}

static void report_progress(struct progress_reporter *progress, uint32_t percent) {
    char message[256];
    int length;
    size_t written = 0;

    fprintf(stderr, "super.img progress: %u%% (%" PRIu64 " / %" PRIu64 " MiB)\n",
            percent, progress->produced / (1024U * 1024U),
            progress->total_bytes / (1024U * 1024U));
    fflush(stderr);

    if (progress->ui_fd < 0) return;

#ifdef BOXED_PROGRESS
    {
        char text[32];
        int text_length;
        int left_pad;
        int right_pad;

        text_length = snprintf(text, sizeof(text), "super.img progress: %u%%", percent);
        if (text_length <= 0 || text_length > 31) {
            progress->ui_fd = -1;
            return;
        }
        left_pad = (31 - text_length) / 2;
        right_pad = 31 - text_length - left_pad;
        length = snprintf(message, sizeof(message),
                          "ui_print ###################################\n"
                          "ui_print # %*s%s%*s #\n"
                          "ui_print ###################################\n"
                          "ui_print\n",
                          left_pad, "", text, right_pad, "");
    }
#else
    length = snprintf(message, sizeof(message),
                      "ui_print super.img progress: %u%% (%" PRIu64 " / %" PRIu64
                      " MiB)\nui_print\n",
                      percent, progress->produced / (1024U * 1024U),
                      progress->total_bytes / (1024U * 1024U));
#endif
    if (length <= 0 || (size_t)length >= sizeof(message)) {
        progress->ui_fd = -1;
        return;
    }

    while (written < (size_t)length) {
        ssize_t result = write(progress->ui_fd, message + written, (size_t)length - written);
        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result <= 0) {
            progress->ui_fd = -1;
            return;
        }
        written += (size_t)result;
    }
}

static void advance_progress(struct progress_reporter *progress, uint64_t amount) {
    uint32_t percent;

    progress->produced += amount;
    percent = (uint32_t)((progress->produced * 100U) / progress->total_bytes);

    while (percent >= progress->next_percent && progress->next_percent <= 100U) {
        report_progress(progress, progress->next_percent);
        progress->next_percent += PROGRESS_STEP_PERCENT;
    }
}

static int copy_exact(int input, int output, uint64_t size, uint8_t *buffer,
                      struct progress_reporter *progress) {
    while (size != 0) {
        size_t amount = size > BUFFER_SIZE ? BUFFER_SIZE : (size_t)size;
        if (read_exact(input, buffer, amount) != 0 || write_all(output, buffer, amount) != 0) {
            return -1;
        }
        advance_progress(progress, amount);
        size -= amount;
    }
    return 0;
}

static int write_pattern(int output, uint64_t size, const uint8_t pattern[4], uint8_t *buffer,
                         struct progress_reporter *progress) {
    size_t i;

    for (i = 0; i < BUFFER_SIZE; i += 4) {
        memcpy(buffer + i, pattern, 4);
    }

    while (size != 0) {
        size_t amount = size > BUFFER_SIZE ? BUFFER_SIZE : (size_t)size;
        if (write_all(output, buffer, amount) != 0) {
            return -1;
        }
        advance_progress(progress, amount);
        size -= amount;
    }
    return 0;
}

static int parse_u64(const char *text, uint64_t *value) {
    char *end = NULL;
    unsigned long long parsed;

    errno = 0;
    parsed = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') {
        return -1;
    }
    *value = (uint64_t)parsed;
    return 0;
}

static int parse_fd(const char *text, int *value) {
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || parsed < -1 || parsed > INT_MAX) {
        return -1;
    }
    *value = (int)parsed;
    return 0;
}

int main(int argc, char **argv) {
    uint8_t header[SPARSE_HEADER_LEN];
    uint8_t chunk_header[CHUNK_HEADER_LEN];
    uint8_t fill_pattern[4];
    uint8_t *buffer = NULL;
    uint64_t output_offset;
    uint64_t target_capacity;
    uint64_t total_bytes;
    struct progress_reporter progress = {.ui_fd = -1};
    uint32_t block_size;
    uint32_t total_blocks;
    uint32_t total_chunks;
    uint16_t file_header_size;
    uint16_t chunk_header_size;
    int output = -1;
    uint32_t index;
    int result = 1;

    if (argc != 4 && argc != 5) {
        fprintf(stderr, "Usage: %s OUTPUT OFFSET_BYTES TARGET_CAPACITY_BYTES [RECOVERY_UI_FD]\n",
                argv[0]);
        return 2;
    }

    if (parse_u64(argv[2], &output_offset) != 0 ||
        parse_u64(argv[3], &target_capacity) != 0) {
        fprintf(stderr, "Invalid offset or target capacity\n");
        return 2;
    }
    if (argc == 5 && parse_fd(argv[4], &progress.ui_fd) != 0) {
        fprintf(stderr, "Invalid recovery UI file descriptor\n");
        return 2;
    }

    signal(SIGPIPE, SIG_IGN);
    fprintf(stderr, "streamer started: output=%s offset=%s capacity=%s ui_fd=%d\n", argv[1],
            argv[2], argv[3], progress.ui_fd);

    if (read_exact(STDIN_FILENO, header, sizeof(header)) != 0) {
        goto done;
    }

    if (get_le32(header) != SPARSE_HEADER_MAGIC) {
        fprintf(stderr, "Input is not an Android sparse image\n");
        goto done;
    }
    if (get_le16(header + 4) != 1) {
        fprintf(stderr, "Unsupported sparse major version: %u\n", get_le16(header + 4));
        goto done;
    }

    file_header_size = get_le16(header + 8);
    chunk_header_size = get_le16(header + 10);
    block_size = get_le32(header + 12);
    total_blocks = get_le32(header + 16);
    total_chunks = get_le32(header + 20);

    fprintf(stderr,
            "sparse header: file_header=%u chunk_header=%u block_size=%u blocks=%u chunks=%u\n",
            file_header_size, chunk_header_size, block_size, total_blocks, total_chunks);

    if (file_header_size < SPARSE_HEADER_LEN || chunk_header_size < CHUNK_HEADER_LEN ||
        block_size == 0) {
        fprintf(stderr, "Invalid sparse header sizes\n");
        goto done;
    }

    total_bytes = (uint64_t)block_size * (uint64_t)total_blocks;
    if (output_offset > target_capacity ||
        total_bytes > target_capacity - output_offset) {
        fprintf(stderr,
                "Expanded sparse range (%" PRIu64 " + %" PRIu64
                ") exceeds target capacity %" PRIu64 "\n",
                output_offset, total_bytes, target_capacity);
        goto done;
    }
    progress.total_bytes = total_bytes;
    progress.next_percent = PROGRESS_STEP_PERCENT;
    if (output_offset > INT64_MAX || total_bytes > INT64_MAX - output_offset) {
        fprintf(stderr, "Output range overflows off_t\n");
        goto done;
    }

    buffer = malloc(BUFFER_SIZE);
    if (buffer == NULL) {
        fprintf(stderr, "Unable to allocate transfer buffer\n");
        goto done;
    }

    if (discard_exact(STDIN_FILENO, file_header_size - SPARSE_HEADER_LEN, buffer) != 0) {
        goto done;
    }

    output = open(argv[1], O_WRONLY);
    if (output < 0) {
        fprintf(stderr, "Cannot open %s: %s\n", argv[1], strerror(errno));
        goto done;
    }
    if (lseek(output, (off_t)output_offset, SEEK_SET) < 0) {
        fprintf(stderr, "Cannot seek output to %" PRIu64 ": %s\n", output_offset,
                strerror(errno));
        goto done;
    }

    report_progress(&progress, 0U);

    for (index = 0; index < total_chunks; ++index) {
        uint16_t chunk_type;
        uint32_t chunk_blocks;
        uint32_t chunk_total_size;
        uint64_t chunk_bytes;
        uint64_t payload_size;

        if (read_exact(STDIN_FILENO, chunk_header, sizeof(chunk_header)) != 0) {
            goto done;
        }

        chunk_type = get_le16(chunk_header);
        chunk_blocks = get_le32(chunk_header + 4);
        chunk_total_size = get_le32(chunk_header + 8);
        chunk_bytes = (uint64_t)chunk_blocks * (uint64_t)block_size;

        if (chunk_total_size < chunk_header_size) {
            fprintf(stderr, "Chunk %u has an invalid total size\n", index);
            goto done;
        }
        payload_size = (uint64_t)chunk_total_size - chunk_header_size;

        if (discard_exact(STDIN_FILENO, chunk_header_size - CHUNK_HEADER_LEN, buffer) != 0) {
            goto done;
        }
        if (chunk_bytes > total_bytes - progress.produced) {
            fprintf(stderr, "Chunk %u exceeds the expected output size\n", index);
            goto done;
        }

        switch (chunk_type) {
            case CHUNK_TYPE_RAW:
                if (payload_size != chunk_bytes) {
                    fprintf(stderr, "RAW chunk %u has an invalid payload size\n", index);
                    goto done;
                }
                if (copy_exact(STDIN_FILENO, output, chunk_bytes, buffer, &progress) != 0) {
                    goto done;
                }
                break;

            case CHUNK_TYPE_FILL:
                if (payload_size != sizeof(fill_pattern)) {
                    fprintf(stderr, "FILL chunk %u has an invalid payload size\n", index);
                    goto done;
                }
                if (read_exact(STDIN_FILENO, fill_pattern, sizeof(fill_pattern)) != 0 ||
                    write_pattern(output, chunk_bytes, fill_pattern, buffer, &progress) != 0) {
                    goto done;
                }
                break;

            case CHUNK_TYPE_DONT_CARE:
                if (payload_size != 0) {
                    fprintf(stderr, "DONT_CARE chunk %u has an invalid payload size\n", index);
                    goto done;
                }
                memset(fill_pattern, 0, sizeof(fill_pattern));
                if (write_pattern(output, chunk_bytes, fill_pattern, buffer, &progress) != 0) {
                    goto done;
                }
                break;

            case CHUNK_TYPE_CRC32:
                if (chunk_bytes != 0 || payload_size != 4) {
                    fprintf(stderr, "CRC32 chunk %u is invalid\n", index);
                    goto done;
                }
                if (read_exact(STDIN_FILENO, fill_pattern, 4) != 0) {
                    goto done;
                }
                break;

            default:
                fprintf(stderr, "Unsupported sparse chunk type 0x%04x at chunk %u\n",
                        chunk_type, index);
                goto done;
        }

    }

    if (progress.produced != total_bytes) {
        fprintf(stderr, "Sparse output is %" PRIu64 " bytes, expected %" PRIu64 "\n",
                progress.produced, total_bytes);
        goto done;
    }

    for (;;) {
        uint8_t extra;
        ssize_t n = read(STDIN_FILENO, &extra, 1);
        if (n == 0) {
            break;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n < 0) {
            fprintf(stderr, "Failed checking end of sparse stream: %s\n", strerror(errno));
        } else {
            fprintf(stderr, "Unexpected trailing data after sparse image\n");
        }
        goto done;
    }

    fprintf(stderr, "expanded data written; starting fsync\n");
    fflush(stderr);
    if (fsync(output) != 0) {
        fprintf(stderr, "fsync failed: %s\n", strerror(errno));
        goto done;
    }
    fprintf(stderr, "fsync complete; streamer finished successfully\n");

    result = 0;

done:
    if (output >= 0) {
        close(output);
    }
    free(buffer);
    return result;
}
