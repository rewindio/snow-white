#!/bin/bash

echo "Waking the Sidekiq workers"

REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone | sed 's/\(.*\)[a-z]/\1/')

# Returns the PID of the worker process
get_pid() {
    local __workerinfo=$1
    pid=$(echo ${__workerinfo} |cut -f 1 -d' ')
    echo ${pid}
}

# Wakes a worker (kill and let it be restarted)
wake_worker() {
    local __workerinfo=$1
    PID=$(get_pid $__workerinfo)
    kill -TERM ${PID}
}

# Returns the number of currently running jobs
get_running_count() {
    local __workerinfo=$1
    count=$(echo ${__workerinfo} |cut -f 5 -d' ' |cut -f2 -d [)
    echo ${count}
}

# Checks to see if the worker is in the stopping state
is_worker_stopping() {
    local __workerinfo=$1
    stopping=$(echo ${__workerinfo} |cut -f 9 -d' ')

    echo "${stopping}"
}

#
# MAINLINE
# 

sidekiq_workers=$(pgrep -lfa ^sidekiq)

IFS=$'\n'
for worker in ${sidekiq_workers}
do
    worker=$(echo ${worker}|tr -d '\n' |xargs)
    echo  "Waking worker ${worker}"

    # Make sure it's in the stopping state
    # If not, this is an error condition as we were 
    # called on a non-quieted worker
    if [ "$(is_worker_stopping "${worker}")" != "stopping" ]; then
        echo "Worker ${worker_pid} is not in a STOPPING state.  Will not wake"
    else
        running_count=$(get_running_count ${worker})
        echo "Worker ${worker_pid} is running ${running_count} jobs"

        if [ ${running_count} -eq 0 ]; then
            wake_worker "${worker}"
        else
            echo "Worker ${worker_pid} is still running ${running_count} - you cannot wake/restart this worker yet"
            exit 2
        fi
    fi
done
unset IFS
