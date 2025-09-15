#!/usr/bin/env python3
"""
Parallel Build Checker for Lean Files
Builds Lean files in parallel using `lake env lean --make` and collects failures.
"""

import subprocess
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime

# Thread-safe logging
log_lock = threading.Lock()

def log_message(message):
    """Thread-safe logging with timestamps"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    with log_lock:
        print(f"[{timestamp}] {message}")

def build_lean_file(file_path, output_dir):
    """
    Build a single Lean file using lake env lean --make
    Returns (block_id, success, stdout, stderr)
    """
    block_id = file_path.stem  # e.g., "Block_017"
    
    try:
        # Use lake env to ensure proper environment setup
        # Lean 4.16 does not support `--make`.
        # Use `--root=.` to set the package root and elaborate the file.
        project_root = file_path.parent.parent
        cmd = [
            "lake", "env", "lean",
            f"--root={str(project_root)}",
            str(file_path)
        ]
        
        log_message(f"Building {block_id}...")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,  # 60 second timeout per file
            cwd=str(project_root)  # brickMove directory
        )
        
        success = result.returncode == 0
        
        # Save individual logs
        log_file = output_dir / f"{block_id}_build.log"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"Command: {' '.join(cmd)}\n")
            f.write(f"Return Code: {result.returncode}\n")
            f.write(f"Success: {success}\n")
            f.write("\n--- STDOUT ---\n")
            f.write(result.stdout)
            f.write("\n--- STDERR ---\n")
            f.write(result.stderr)
        
        status = "✓" if success else "✗"
        log_message(f"{status} {block_id} ({'OK' if success else 'FAILED'})")
        
        return block_id, success, result.stdout, result.stderr, str(log_file)
        
    except subprocess.TimeoutExpired:
        log_message(f"✗ {block_id} (TIMEOUT)")
        return block_id, False, "", "Build timeout after 60 seconds", ""
    except Exception as e:
        log_message(f"✗ {block_id} (ERROR: {str(e)})")
        return block_id, False, "", f"Build error: {str(e)}", ""

def run_parallel_build_check(blocks_dir, output_dir, block_range=None, max_workers=4):
    """
    Run parallel build check on Lean files
    
    Args:
        blocks_dir: Path to directory containing Block_*.lean files
        output_dir: Path to directory for logs and results
        block_range: Tuple (start, end) for block numbers to check, or None for all
        max_workers: Number of parallel workers
    """
    blocks_path = Path(blocks_dir)
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    # Find all Block_*.lean files
    lean_files = list(blocks_path.glob("Block_*.lean"))
    
    # Filter by range if specified
    if block_range:
        start_num, end_num = block_range
        lean_files = [
            f for f in lean_files 
            if start_num <= int(f.stem.split('_')[1]) <= end_num
        ]
    
    lean_files.sort()
    
    log_message(f"Found {len(lean_files)} Lean files to check")
    log_message(f"Using {max_workers} parallel workers")
    log_message(f"Logs will be saved to: {output_path}")
    
    # Results tracking
    results = []
    successful_builds = []
    failed_builds = []
    
    # Run parallel builds
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_file = {
            executor.submit(build_lean_file, file_path, output_path): file_path
            for file_path in lean_files
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_file):
            block_id, success, stdout, stderr, log_file = future.result()
            
            result_entry = {
                "block_id": block_id,
                "success": success,
                "log_file": log_file,
                "has_errors": bool(stderr.strip()),
                "stdout_lines": len(stdout.splitlines()),
                "stderr_lines": len(stderr.splitlines())
            }
            
            results.append(result_entry)
            
            if success:
                successful_builds.append(block_id)
            else:
                failed_builds.append(block_id)
    
    # Generate summary report
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_files": len(lean_files),
        "successful_builds": len(successful_builds),
        "failed_builds": len(failed_builds),
        "success_rate": len(successful_builds) / len(lean_files) * 100 if lean_files else 0,
        "successful_blocks": successful_builds,
        "failed_blocks": failed_builds,
        "detailed_results": results
    }
    
    # Save summary
    summary_file = output_path / "build_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    # Print final summary
    log_message("=" * 50)
    log_message("BUILD CHECK SUMMARY")
    log_message("=" * 50)
    log_message(f"Total files checked: {len(lean_files)}")
    log_message(f"Successful builds: {len(successful_builds)}")
    log_message(f"Failed builds: {len(failed_builds)}")
    log_message(f"Success rate: {summary['success_rate']:.1f}%")
    
    if failed_builds:
        log_message(f"\nFailed blocks: {', '.join(failed_builds)}")
    
    log_message(f"\nDetailed results saved to: {summary_file}")
    log_message(f"Individual logs saved to: {output_path}")
    
    return summary

def main():
    """Main function for command-line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Parallel Build Checker for Lean Files")
    parser.add_argument("--blocks-dir", default="sfs4_new_blocks", 
                       help="Directory containing Block_*.lean files")
    parser.add_argument("--output-dir", default="build_check_logs", 
                       help="Directory for logs and results")
    parser.add_argument("--range", type=str, 
                       help="Block range to check (e.g., '17-100')")
    parser.add_argument("--workers", type=int, default=4, 
                       help="Number of parallel workers")
    
    args = parser.parse_args()
    
    # Parse range
    block_range = None
    if args.range:
        try:
            start_str, end_str = args.range.split('-')
            block_range = (int(start_str), int(end_str))
        except ValueError:
            print(f"Invalid range format: {args.range}. Use format like '17-100'")
            return 1
    
    # Run the build check
    try:
        summary = run_parallel_build_check(
            args.blocks_dir, 
            args.output_dir, 
            block_range, 
            args.workers
        )
        
        # Exit with error code if there were failures
        return 1 if summary['failed_builds'] else 0
        
    except Exception as e:
        log_message(f"Error running build check: {e}")
        return 1

if __name__ == "__main__":
    exit(main())