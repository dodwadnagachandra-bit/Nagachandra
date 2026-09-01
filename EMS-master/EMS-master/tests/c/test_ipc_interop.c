#include "ipc_defs.h"
#include "ems_types.h"
#include "mpack.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------------------
 * Encode helpers -- each writes one sample message into the mpack writer.
 * -------------------------------------------------------------------------*/

static void encode_telemetry_sample(mpack_writer_t *writer)
{
    mpack_start_map(writer, 5);

    mpack_write_cstr(writer, EMS_MSG_KEY_TIMESTAMP);
    mpack_write_u64(writer, 1234567890ULL);

    mpack_write_cstr(writer, EMS_MSG_KEY_SEQUENCE);
    mpack_write_u32(writer, 42);

    mpack_write_cstr(writer, EMS_MSG_KEY_SOURCE);
    mpack_write_cstr(writer, "data_manager");

    mpack_write_cstr(writer, EMS_MSG_KEY_TOPIC);
    mpack_write_cstr(writer, EMS_TOPIC_PCS);

    mpack_write_cstr(writer, EMS_MSG_KEY_PAYLOAD);
    mpack_start_map(writer, 5);
    mpack_write_cstr(writer, "ac_voltage");
    mpack_write_double(writer, 230.5);
    mpack_write_cstr(writer, "active_power");
    mpack_write_double(writer, 100.0);
    mpack_write_cstr(writer, "frequency");
    mpack_write_double(writer, 50.01);
    mpack_write_cstr(writer, "state");
    mpack_write_i32(writer, PCS_STATE_RUNNING);
    mpack_write_cstr(writer, "fault_code");
    mpack_write_u32(writer, 0);
    mpack_finish_map(writer);

    mpack_finish_map(writer);
}

static void encode_command_request_sample(mpack_writer_t *writer)
{
    mpack_start_map(writer, 2);

    mpack_write_cstr(writer, EMS_MSG_KEY_ACTION);
    mpack_write_cstr(writer, "set_mode");

    mpack_write_cstr(writer, EMS_MSG_KEY_PARAMS);
    mpack_start_map(writer, 1);
    mpack_write_cstr(writer, "mode");
    mpack_write_cstr(writer, "charging");
    mpack_finish_map(writer);

    mpack_finish_map(writer);
}

static void encode_command_response_sample(mpack_writer_t *writer)
{
    mpack_start_map(writer, 3);

    mpack_write_cstr(writer, EMS_MSG_KEY_STATUS);
    mpack_write_cstr(writer, EMS_STATUS_OK);

    mpack_write_cstr(writer, EMS_MSG_KEY_RESULT);
    mpack_start_map(writer, 1);
    mpack_write_cstr(writer, "previous_mode");
    mpack_write_cstr(writer, "idle");
    mpack_finish_map(writer);

    mpack_write_cstr(writer, EMS_MSG_KEY_ERROR_MSG);
    mpack_write_nil(writer);

    mpack_finish_map(writer);
}

static void encode_event_sample(mpack_writer_t *writer)
{
    mpack_start_map(writer, 6);

    mpack_write_cstr(writer, EMS_MSG_KEY_TIMESTAMP);
    mpack_write_u64(writer, 1234567890ULL);

    mpack_write_cstr(writer, EMS_MSG_KEY_SOURCE);
    mpack_write_cstr(writer, "alarm_manager");

    mpack_write_cstr(writer, EMS_MSG_KEY_SEVERITY);
    mpack_write_cstr(writer, EMS_SEVERITY_STR_WARNING);

    mpack_write_cstr(writer, EMS_MSG_KEY_EVENT_TYPE);
    mpack_write_cstr(writer, EMS_TOPIC_ALARM);

    mpack_write_cstr(writer, EMS_MSG_KEY_MESSAGE);
    mpack_write_cstr(writer, "Cell voltage high on rack 3");

    mpack_write_cstr(writer, EMS_MSG_KEY_DATA);
    mpack_start_map(writer, 3);
    mpack_write_cstr(writer, "rack_id");
    mpack_write_i32(writer, 3);
    mpack_write_cstr(writer, "cell_id");
    mpack_write_i32(writer, 15);
    mpack_write_cstr(writer, "voltage");
    mpack_write_double(writer, 3.65);
    mpack_finish_map(writer);

    mpack_finish_map(writer);
}

/* ---------------------------------------------------------------------------
 * Self-test: encode then decode in C, verify key fields.
 * -------------------------------------------------------------------------*/

static int self_test(void)
{
    int failures = 0;

    /* --- Telemetry roundtrip --- */
    {
        char *buf = NULL;
        size_t size = 0;
        mpack_writer_t writer;
        mpack_writer_init_growable(&writer, &buf, &size);
        encode_telemetry_sample(&writer);
        if (mpack_writer_destroy(&writer) != mpack_ok)
        {
            fprintf(stderr, "FAIL: telemetry encode error\n");
            return 1;
        }

        mpack_reader_t reader;
        mpack_reader_init_data(&reader, buf, size);

        mpack_expect_map_max(&reader, 10);
        /* ts */
        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_TIMESTAMP);
        uint64_t ts = mpack_expect_u64(&reader);
        /* seq */
        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_SEQUENCE);
        uint32_t seq = mpack_expect_u32(&reader);
        /* src */
        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_SOURCE);
        char src[64];
        mpack_expect_cstr(&reader, src, sizeof(src));
        /* topic */
        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_TOPIC);
        char topic[64];
        mpack_expect_cstr(&reader, topic, sizeof(topic));
        /* payload -- skip it for roundtrip check */
        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_PAYLOAD);
        mpack_discard(&reader);

        mpack_done_map(&reader);

        if (mpack_reader_destroy(&reader) != mpack_ok)
        {
            fprintf(stderr, "FAIL: telemetry decode error\n");
            free(buf);
            return 1;
        }

        if (ts != 1234567890ULL)
        {
            fprintf(stderr, "FAIL: telemetry ts=%llu expected 1234567890\n",
                    (unsigned long long)ts);
            failures++;
        }
        if (seq != 42)
        {
            fprintf(stderr, "FAIL: telemetry seq=%u expected 42\n", seq);
            failures++;
        }
        if (strcmp(src, "data_manager") != 0)
        {
            fprintf(stderr, "FAIL: telemetry src=%s expected data_manager\n", src);
            failures++;
        }
        if (strcmp(topic, "pcs") != 0)
        {
            fprintf(stderr, "FAIL: telemetry topic=%s expected pcs\n", topic);
            failures++;
        }

        free(buf);
        printf("telemetry roundtrip: %s\n", failures == 0 ? "PASS" : "FAIL");
    }

    /* --- Command request roundtrip --- */
    {
        char *buf = NULL;
        size_t size = 0;
        mpack_writer_t writer;
        mpack_writer_init_growable(&writer, &buf, &size);
        encode_command_request_sample(&writer);
        if (mpack_writer_destroy(&writer) != mpack_ok)
        {
            fprintf(stderr, "FAIL: cmd request encode error\n");
            return 1;
        }

        mpack_reader_t reader;
        mpack_reader_init_data(&reader, buf, size);
        mpack_expect_map_max(&reader, 5);

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_ACTION);
        char action[64];
        mpack_expect_cstr(&reader, action, sizeof(action));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_PARAMS);
        mpack_discard(&reader);

        mpack_done_map(&reader);

        if (mpack_reader_destroy(&reader) != mpack_ok)
        {
            fprintf(stderr, "FAIL: cmd request decode error\n");
            free(buf);
            return 1;
        }

        if (strcmp(action, "set_mode") != 0)
        {
            fprintf(stderr, "FAIL: cmd request action=%s expected set_mode\n", action);
            failures++;
        }

        free(buf);
        printf("command request roundtrip: %s\n", failures == 0 ? "PASS" : "FAIL");
    }

    /* --- Command response roundtrip --- */
    {
        char *buf = NULL;
        size_t size = 0;
        mpack_writer_t writer;
        mpack_writer_init_growable(&writer, &buf, &size);
        encode_command_response_sample(&writer);
        if (mpack_writer_destroy(&writer) != mpack_ok)
        {
            fprintf(stderr, "FAIL: cmd response encode error\n");
            return 1;
        }

        mpack_reader_t reader;
        mpack_reader_init_data(&reader, buf, size);
        mpack_expect_map_max(&reader, 5);

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_STATUS);
        char status[64];
        mpack_expect_cstr(&reader, status, sizeof(status));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_RESULT);
        mpack_discard(&reader);

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_ERROR_MSG);
        mpack_expect_nil(&reader);

        mpack_done_map(&reader);

        if (mpack_reader_destroy(&reader) != mpack_ok)
        {
            fprintf(stderr, "FAIL: cmd response decode error\n");
            free(buf);
            return 1;
        }

        if (strcmp(status, "ok") != 0)
        {
            fprintf(stderr, "FAIL: cmd response status=%s expected ok\n", status);
            failures++;
        }

        free(buf);
        printf("command response roundtrip: %s\n", failures == 0 ? "PASS" : "FAIL");
    }

    /* --- Event roundtrip --- */
    {
        char *buf = NULL;
        size_t size = 0;
        mpack_writer_t writer;
        mpack_writer_init_growable(&writer, &buf, &size);
        encode_event_sample(&writer);
        if (mpack_writer_destroy(&writer) != mpack_ok)
        {
            fprintf(stderr, "FAIL: event encode error\n");
            return 1;
        }

        mpack_reader_t reader;
        mpack_reader_init_data(&reader, buf, size);
        mpack_expect_map_max(&reader, 10);

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_TIMESTAMP);
        uint64_t ts = mpack_expect_u64(&reader);

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_SOURCE);
        char source[64];
        mpack_expect_cstr(&reader, source, sizeof(source));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_SEVERITY);
        char severity[64];
        mpack_expect_cstr(&reader, severity, sizeof(severity));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_EVENT_TYPE);
        char event_type[64];
        mpack_expect_cstr(&reader, event_type, sizeof(event_type));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_MESSAGE);
        char message[256];
        mpack_expect_cstr(&reader, message, sizeof(message));

        mpack_expect_cstr_match(&reader, EMS_MSG_KEY_DATA);
        mpack_discard(&reader);

        mpack_done_map(&reader);

        if (mpack_reader_destroy(&reader) != mpack_ok)
        {
            fprintf(stderr, "FAIL: event decode error\n");
            free(buf);
            return 1;
        }

        if (ts != 1234567890ULL)
        {
            fprintf(stderr, "FAIL: event ts=%llu expected 1234567890\n",
                    (unsigned long long)ts);
            failures++;
        }
        if (strcmp(severity, "warning") != 0)
        {
            fprintf(stderr, "FAIL: event severity=%s expected warning\n", severity);
            failures++;
        }
        if (strcmp(event_type, "alarm") != 0)
        {
            fprintf(stderr, "FAIL: event event_type=%s expected alarm\n", event_type);
            failures++;
        }

        free(buf);
        printf("event roundtrip: %s\n", failures == 0 ? "PASS" : "FAIL");
    }

    if (failures > 0)
    {
        fprintf(stderr, "\n%d assertion(s) failed\n", failures);
        return 1;
    }

    printf("\nAll self-tests passed.\n");
    return 0;
}

/* ---------------------------------------------------------------------------
 * Output mode: write 4 length-prefixed messages to a file.
 * Each message: [4-byte big-endian length][msgpack bytes]
 * -------------------------------------------------------------------------*/

typedef void (*encode_fn)(mpack_writer_t *);

static int write_message(FILE *fp, encode_fn fn)
{
    char *buf = NULL;
    size_t size = 0;
    mpack_writer_t writer;
    mpack_writer_init_growable(&writer, &buf, &size);
    fn(&writer);
    if (mpack_writer_destroy(&writer) != mpack_ok)
    {
        fprintf(stderr, "encode error\n");
        free(buf);
        return -1;
    }

    /* Write 4-byte big-endian length prefix */
    uint32_t len = (uint32_t)size;
    unsigned char hdr[4];
    hdr[0] = (unsigned char)((len >> 24) & 0xFF);
    hdr[1] = (unsigned char)((len >> 16) & 0xFF);
    hdr[2] = (unsigned char)((len >> 8) & 0xFF);
    hdr[3] = (unsigned char)(len & 0xFF);

    if (fwrite(hdr, 1, 4, fp) != 4 || fwrite(buf, 1, size, fp) != size)
    {
        fprintf(stderr, "write error\n");
        free(buf);
        return -1;
    }

    printf("  wrote %u bytes\n", len);
    free(buf);
    return 0;
}

static int output_mode(const char *path)
{
    FILE *fp = fopen(path, "wb");
    if (!fp)
    {
        perror("fopen");
        return 1;
    }

    printf("Writing 4 sample messages to %s:\n", path);

    encode_fn fns[] = {
        encode_telemetry_sample,
        encode_command_request_sample,
        encode_command_response_sample,
        encode_event_sample,
    };
    const char *names[] = {
        "telemetry",
        "command_request",
        "command_response",
        "event",
    };

    for (int i = 0; i < 4; i++)
    {
        printf("  [%d] %s:", i, names[i]);
        if (write_message(fp, fns[i]) != 0)
        {
            fclose(fp);
            return 1;
        }
    }

    fclose(fp);
    printf("Done.\n");
    return 0;
}

/* ---------------------------------------------------------------------------
 * main
 * -------------------------------------------------------------------------*/

int main(int argc, char *argv[])
{
    if (argc < 2 || strcmp(argv[1], "--self-test") == 0)
    {
        return self_test();
    }

    if (strcmp(argv[1], "--output") == 0)
    {
        if (argc < 3)
        {
            fprintf(stderr, "Usage: %s --output <file>\n", argv[0]);
            return 1;
        }
        return output_mode(argv[2]);
    }

    fprintf(stderr, "Usage: %s [--self-test | --output <file>]\n", argv[0]);
    return 1;
}
