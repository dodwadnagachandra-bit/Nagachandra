#ifndef EMS_SEQLOCK_H
#define EMS_SEQLOCK_H

/**
 * @file seqlock.h
 * @brief Lock-free seqlock primitive for RTDB concurrent access.
 *
 * Seqlock protocol:
 *   - Sequence counter is an _Atomic uint32_t, starts at 0 (even).
 *   - Writer increments to odd before writing, even after writing.
 *   - Reader snapshots the sequence before reading. If odd, spins until even.
 *     After reading, checks if sequence changed — if so, data was modified
 *     during the read and the reader must retry.
 *
 * Readers never block. Writers must be externally serialized
 * (single-writer-per-section enforced by data_manager at runtime).
 *
 * Memory ordering:
 *   - Writers use release semantics to ensure data stores are visible
 *     before the sequence update is seen by readers.
 *   - Readers use acquire semantics to ensure they see the latest data
 *     after observing the sequence value.
 */

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>

typedef struct
{
    _Atomic uint32_t sequence;
} ems_seqlock_t;

/**
 * @brief Initialize a seqlock to sequence 0 (no write in progress).
 * @param lock Pointer to the seqlock.
 */
static inline void ems_seqlock_init(ems_seqlock_t *lock)
{
    atomic_store_explicit(&lock->sequence, 0, memory_order_relaxed);
}

/**
 * @brief Begin a write section. Increments sequence to odd.
 * @param lock Pointer to the seqlock.
 *
 * Must be paired with ems_seqlock_write_end(). The caller must hold
 * exclusive write access (single-writer-per-section).
 */
static inline void ems_seqlock_write_begin(ems_seqlock_t *lock)
{
    uint32_t seq = atomic_load_explicit(&lock->sequence, memory_order_relaxed);
    atomic_store_explicit(&lock->sequence, seq + 1, memory_order_relaxed);
    atomic_thread_fence(memory_order_release);
}

/**
 * @brief End a write section. Increments sequence to even.
 * @param lock Pointer to the seqlock.
 */
static inline void ems_seqlock_write_end(ems_seqlock_t *lock)
{
    atomic_thread_fence(memory_order_release);
    uint32_t seq = atomic_load_explicit(&lock->sequence, memory_order_relaxed);
    atomic_store_explicit(&lock->sequence, seq + 1, memory_order_release);
}

/**
 * @brief Begin a read section. Returns the current sequence value.
 * @param lock Pointer to the seqlock.
 * @return Sequence value (even). Spins if a write is in progress (odd).
 *
 * After copying the data, call ems_seqlock_read_retry() with the returned
 * value to check if the data was modified during the read.
 */
static inline uint32_t ems_seqlock_read_begin(const ems_seqlock_t *lock)
{
    uint32_t seq;
    for (;;)
    {
        seq = atomic_load_explicit(&lock->sequence, memory_order_acquire);
        if ((seq & 1) == 0)
        {
            break;
        }
        /* Writer in progress — spin */
    }
    atomic_thread_fence(memory_order_acquire);
    return seq;
}

/**
 * @brief Check if data was modified during a read section.
 * @param lock Pointer to the seqlock.
 * @param seq  Sequence value returned by ems_seqlock_read_begin().
 * @return true if data was modified (must retry), false if read was consistent.
 */
static inline bool ems_seqlock_read_retry(const ems_seqlock_t *lock, uint32_t seq)
{
    atomic_thread_fence(memory_order_acquire);
    return atomic_load_explicit(&lock->sequence, memory_order_acquire) != seq;
}

#endif /* EMS_SEQLOCK_H */
