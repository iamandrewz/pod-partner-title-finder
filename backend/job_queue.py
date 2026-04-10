"""
Job Queue System for Max-Powered Episode Optimizer
===================================================
Manages jobs stored as JSON files in /pursue-segments/backend/jobs/

Job States:
- pending: Job created, waiting for worker
- processing: Worker is currently processing
- complete: Job finished successfully
- error: Job failed

Author: Subagent
Date: 2026-02-18
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, Optional, List

# Jobs directory
JOBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobs')

# Ensure jobs directory exists
os.makedirs(JOBS_DIR, exist_ok=True)


def _get_job_path(job_id: str) -> str:
    """Get full path for job file."""
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def create_job(youtube_url: str, podcast: str, outputs: List[str], metadata: Optional[Dict[str, Any]] = None, mode: str = None, job_id: str = None) -> str:
    """
    Create a new optimization job.
    
    Args:
        youtube_url: YouTube URL to optimize
        podcast: Podcast code (spp, jpi, sbs, wow, agp)
        outputs: List of requested outputs
        metadata: Optional additional metadata
        mode: Optional job mode ('titles_only', 'outputs_only', None for full)
        job_id: Optional custom job_id (for secondary jobs)
    
    Returns:
        job_id: Unique job identifier
    """
    job_id = job_id or str(uuid.uuid4())[:8]
    
    job_data = {
        'job_id': job_id,
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'youtube_url': youtube_url,
        'podcast': podcast,
        'outputs': outputs,
        'metadata': metadata or {},
        'mode': mode,  # 'titles_only', 'outputs_only', or None for full
        'progress': {
            'step': 'queued',
            'message': 'Job queued, waiting for Max...',
            'percent': 0
        },
        'logs': [],
        'results': None,
        'error': None
    }
    
    job_path = _get_job_path(job_id)
    with open(job_path, 'w') as f:
        json.dump(job_data, f, indent=2)
    
    print(f"[JobQueue] Created job {job_id} (mode: {mode}): {youtube_url}")
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """
    Get job by ID.
    
    Args:
        job_id: Job identifier
    
    Returns:
        Job data dict or None if not found
    """
    job_path = _get_job_path(job_id)
    
    if not os.path.exists(job_path):
        return None
    
    try:
        with open(job_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[JobQueue] Error reading job {job_id}: {e}")
        return None


def update_job(job_id: str, updates: Dict[str, Any]) -> bool:
    """
    Update job with arbitrary fields.
    
    Args:
        job_id: Job identifier
        updates: Dictionary of fields to update
    
    Returns:
        True if successful, False otherwise
    """
    job = get_job(job_id)
    if not job:
        print(f"[JobQueue] Job {job_id} not found for update")
        return False
    
    # Merge updates
    job.update(updates)
    job['updated_at'] = datetime.now().isoformat()
    
    job_path = _get_job_path(job_id)
    try:
        with open(job_path, 'w') as f:
            json.dump(job, f, indent=2)
        return True
    except IOError as e:
        print(f"[JobQueue] Error updating job {job_id}: {e}")
        return False


def update_job_status(job_id: str, status: str, progress: Optional[Dict[str, Any]] = None, 
                      results: Optional[Dict[str, Any]] = None, error: Optional[str] = None,
                      add_log: Optional[str] = None) -> bool:
    """
    Update job status and optional fields.
    
    Args:
        job_id: Job identifier
        status: New status (pending, processing, complete, error)
        progress: Optional progress dict
        results: Optional results dict
        error: Optional error message
        add_log: Optional log message to append
    
    Returns:
        True if successful, False otherwise
    """
    job = get_job(job_id)
    if not job:
        return False
    
    job['status'] = status
    job['updated_at'] = datetime.now().isoformat()
    
    if progress:
        job['progress'] = progress
    
    if results:
        job['results'] = results
    
    if error:
        job['error'] = error
    
    if add_log:
        job['logs'].append({
            'timestamp': datetime.now().isoformat(),
            'message': add_log
        })
    
    job_path = _get_job_path(job_id)
    try:
        with open(job_path, 'w') as f:
            json.dump(job, f, indent=2)
        return True
    except IOError as e:
        print(f"[JobQueue] Error updating job {job_id}: {e}")
        return False


def get_pending_jobs() -> List[Dict[str, Any]]:
    """
    Get all pending jobs.
    
    Returns:
        List of pending job dicts
    """
    jobs = []
    
    for filename in os.listdir(JOBS_DIR):
        if filename.endswith('.json'):
            job_path = os.path.join(JOBS_DIR, filename)
            try:
                with open(job_path, 'r') as f:
                    job = json.load(f)
                    if job.get('status') == 'pending':
                        jobs.append(job)
            except (json.JSONDecodeError, IOError):
                continue
    
    # Sort by created_at
    jobs.sort(key=lambda x: x.get('created_at', ''))
    return jobs


def get_processing_jobs() -> List[Dict[str, Any]]:
    """
    Get all currently processing jobs.
    
    Returns:
        List of processing job dicts
    """
    jobs = []
    
    for filename in os.listdir(JOBS_DIR):
        if filename.endswith('.json'):
            job_path = os.path.join(JOBS_DIR, filename)
            try:
                with open(job_path, 'r') as f:
                    job = json.load(f)
                    if job.get('status') == 'processing':
                        jobs.append(job)
            except (json.JSONDecodeError, IOError):
                continue
    
    return jobs


def delete_job(job_id: str) -> bool:
    """
    Delete a job.
    
    Args:
        job_id: Job identifier
    
    Returns:
        True if deleted, False if not found
    """
    job_path = _get_job_path(job_id)
    
    if os.path.exists(job_path):
        try:
            os.remove(job_path)
            return True
        except IOError as e:
            print(f"[JobQueue] Error deleting job {job_id}: {e}")
            return False
    
    return False


def list_jobs(limit: int = 50, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List jobs with optional status filter.
    
    Args:
        limit: Maximum number of jobs to return
        status_filter: Optional status to filter by
    
    Returns:
        List of job dicts
    """
    jobs = []
    
    for filename in os.listdir(JOBS_DIR):
        if filename.endswith('.json'):
            job_path = os.path.join(JOBS_DIR, filename)
            try:
                with open(job_path, 'r') as f:
                    job = json.load(f)
                    if status_filter is None or job.get('status') == status_filter:
                        jobs.append(job)
            except (json.JSONDecodeError, IOError):
                continue
    
    # Sort by updated_at descending
    jobs.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
    return jobs[:limit]
