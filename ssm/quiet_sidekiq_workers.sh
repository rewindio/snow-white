#!/bin/bash

echo "Quieting the Sidekiq workers"

REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone | sed 's/\(.*\)[a-z]/\1/')

# Returns the PID of the worker process
get_pid() {
    local __workerinfo=$1
    pid=$(echo ${__workerinfo} |cut -f 1 -d' ')
    echo ${pid}
}

# Returns the number of currently running jobs
get_running_count() {
    local __workerinfo=$1
    count=$(echo ${__workerinfo} |cut -f 5 -d' ' |cut -f2 -d [)
    echo ${count}
}

#quiets a worker
# input: 17774 sidekiq 5.2.3 current [0 of 10 busy]
quiet_worker() {
    local __workerinfo=$1
    PID=$(get_pid $__workerinfo)

    kill -TSTP ${PID}
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
worker_count=0

IFS=$'\n'
for worker in ${sidekiq_workers}
do
    worker_count=$((worker_count+1))

    worker=$(echo ${worker}|tr -d '\n' |xargs)
    echo  "Quieting worker ${worker}"

    # Quiet the worker
    quiet_worker "${worker}"
done
unset IFS

# Now we can poll the workers to see if they are finished their work
# This can be an infinite loop with an exit because the EC2 run command
# has a timeout
while :
do
    sidekiq_workers=$(pgrep -lfa ^sidekiq) # Re-get the list of workers
    worker_count=$(pgrep -lfa ^sidekiq | wc -l)
    workers_idle=0

    echo "There are ${worker_count} workers running"

    IFS=$'\n'
    for worker in ${sidekiq_workers}
    do
        worker_pid=$(get_pid ${worker})
        echo "Checking to see if worker ${worker_pid} has completed all jobs"

        if [ "$(is_worker_stopping "${worker}")" == "stopping" ]; then
            echo "Worker ${worker_pid} is in a STOPPING state"
        else
            echo "Worker ${worker_pid} is not in a STOPPING state - did something go wrong with the quiet signal?"
        fi

        running_count=$(get_running_count ${worker})
        echo "Worker ${worker_pid} is running ${running_count} jobs"

        if [ ${running_count} -eq 0 ]; then
            # This worker is all done
            workers_idle=$((workers_idle+1))
            echo "There are ${workers_idle} workers now idle"
        fi
    done
    unset IFS

    if [ ${workers_idle} -eq ${worker_count} ]; then
        # All workers are idle - bail from this loop
        break
    fi

    sleep 10
done

echo "All workers are quiet"
