#ifndef RTDB_LIFECYCLE_H
#define RTDB_LIFECYCLE_H

/**
 * @file rtdb_lifecycle.h
 * @brief RTDB shared memory lifecycle API.
 *
 * Provides create/attach/detach/destroy operations for the RTDB shared
 * memory segment. The data_manager_c process owns the segment; all other
 * modules attach as consumers via rtdb_attach().
 *
 * Stale detection: rtdb_create() checks for leftover shm from a previous
 * crash (wrong magic or version) and recreates it.
 */

#include "rtdb.h"

#include <stdint.h>

/** POSIX shared memory name (appears at /dev/shm/ems_rtdb). */
#define RTDB_SHM_NAME "/ems_rtdb"

/**
 * @brief Create the RTDB shared memory segment.
 *
 * If a stale segment exists (wrong magic or version), it is destroyed
 * and recreated. The new segment is zero-filled, then initialized with
 * the magic, version, topology counts, and all seqlocks set to 0.
 *
 * @param clusters         Number of active clusters.
 * @param racks_per_cluster Number of racks per cluster.
 * @param modules_per_rack  Number of modules per rack.
 * @param cells_per_module  Number of cells per module.
 * @param temps_per_module  Number of temperature sensors per module.
 * @return Pointer to the mapped RTDB, or NULL on failure.
 */
ems_rtdb_t *rtdb_create(uint8_t clusters, uint8_t racks_per_cluster,
                         uint8_t modules_per_rack, uint8_t cells_per_module,
                         uint8_t temps_per_module);

/**
 * @brief Attach to an existing RTDB shared memory segment.
 *
 * Opens the segment read-write, verifies magic and version.
 *
 * @return Pointer to the mapped RTDB, or NULL if not found or invalid.
 */
ems_rtdb_t *rtdb_attach(void);

/**
 * @brief Detach from the RTDB (munmap only, does NOT unlink).
 *
 * @param rtdb Pointer returned by rtdb_create() or rtdb_attach().
 */
void rtdb_detach(ems_rtdb_t *rtdb);

/**
 * @brief Destroy the RTDB shared memory segment (shm_unlink).
 *
 * Called during graceful shutdown by the owning process.
 */
void rtdb_destroy(void);

#endif /* RTDB_LIFECYCLE_H */
