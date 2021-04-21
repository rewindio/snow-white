#!/bin/bash

echo "Stopping the Sidekiq workers"

REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone | sed 's/\(.*\)[a-z]/\1/')

# Returns the PID of the worker process
get_pid() {
    local __workerinfo=$1
    pid=$(echo ${__workerinfo} | cut -f 1 -d' ')
    echo ${pid}
}

# Returns the number of currently running jobs
get_running_count() {
    local __workerinfo=$1
    count=$(echo ${__workerinfo} | cut -f 5 -d' ' | cut -f2 -d [)
    echo ${count}
}

# Checks to see if the worker is in the stopping state
is_worker_stopping() {
    local __workerinfo=$1
    stopping=$(echo ${__workerinfo} | cut -f 9 -d' ')

    echo "${stopping}"
}
#stops a worker gracefully https://github.com/mperham/sidekiq/wiki/Signals#term
term_sidekiq() {
    systemctl kill -s TERM sidekiq
    echo $?
}

# Checks to see if the worker is in the stopping state
sidekiq_running() {
    systemctl is-active --quiet sidekiq
    echo $?
}

#
# MAINLINE
#

if [[ "$(sidekiq_running)" -eq "0" ]]; then
    echo "Sidekiq is running.  Send it a TERM signal to gracefully shut it down"
    term_sidekiq
    sleep 5
else
    echo "Sidekiq is already shutdown"
fi

# Now we can poll the workers to see if they are finished their work
# This can be an infinite loop with an exit because the EC2 run command
# has a timeout
while :; do
    sidekiq_workers=$(pgrep -lfa ^sidekiq) # Re-get the list of workers
    worker_count=$(pgrep -lfa ^sidekiq | wc -l)
    workers_idle=0

    echo "There are ${worker_count} workers running"

    IFS=$'\n'
    for worker in ${sidekiq_workers}; do
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
            workers_idle=$((workers_idle + 1))
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

echo "All workers are stopped"
