
import os
import traceback
from itertools import chain, islice

from openmdao.core.mpi_wrap import debug

trace = os.environ.get('OPENMDAO_TRACE')


def concurrent_eval_lb(func, cases, comm, broadcast=False):
    """
    Runs a load balanced version of the given function, with the master
    rank (0) sending a new case to each worker rank as soon as it
    has finished its last case.

    Args
    ----

    func : function
        The function to execute in workers.

    cases : collection of function args
        Entries are assumed to be of the form (args, kwargs) where
        kwargs are allowed to be None and args should be a list or tuple.

    com : MPI communicator or None
        The MPI communicator that is shared between the master and workers.
        If None, the function will be executed serially.

    broadcast : bool(False)
        If True, the results will be broadcast out to the worker procs so
        that the return value of concurrent_eval_lb will be the full result
        list in every process.
    """
    if comm is not None:
        if comm.rank == 0:  # master rank
            if trace:
                debug('Running Master Rank')
            results = _concurrent_eval_lb_master(cases, comm)
            if trace:
                debug('Master Rank Complete')
        else:
            if trace:
                debug('Running Worker Rank %d' % comm.rank)
            results = _concurrent_eval_lb_worker(func, comm)
            if trace:
                debug('Running Worker Rank %d Complete' % comm.rank)

        if broadcast:
            results = comm.bcast(results, root=0)

    else: # serial execution
        results = []
        for args, kwargs in cases:
            try:
                if kwargs:
                    retval = func(*args, **kwargs)
                else:
                    retval = func(*args)
            except:
                err = traceback.format_exc()
                retval = None
            else:
                err = None
            results.append((retval, err))

    return results

def _concurrent_eval_lb_master(cases, comm):
    """
    This runs only on rank 0.  It sends cases to all of the workers and
    collects their results.
    """
    received = 0
    sent = 0

    results = []

    case_iter = iter(cases)

    # seed the workers
    for i in range(1, comm.size):
        try:
            case = next(case_iter)
        except StopIteration:
            break

        if trace:
            debug('Master sending case', i)
        comm.send(case, i, tag=1)
        if trace:
            debug('Master sent case', i)
        sent += 1

    # send the rest of the cases
    if sent > 0:
        while True:
            # wait for any worker to finish
            worker, retval, err = comm.recv(tag=2)

            received += 1

            # store results
            results.append((retval, err))

            # don't stop until we hear back from every worker process
            # we sent a case to
            if received == sent:
                break

            try:
                case = next(case_iter)
            except StopIteration:
                pass
            else:
                # send new case to the last worker that finished
                comm.send(case, worker, tag=1)
                sent += 1

    # tell all workers to stop
    for rank in range(1, comm.size):
        comm.send((None, None), rank, tag=1)

    return results

def _concurrent_eval_lb_worker(func, comm):
    while True:
        # wait on a case from the master
        if trace:
            debug('Worker Waiting on Case')
        args, kwargs = comm.recv(source=0, tag=1)
        if trace:
            debug('Worker Case Received')

        if args is None: # we're done
            break

        try:
            if kwargs:
                retval = func(*args, **kwargs)
            else:
                retval = func(*args)
        except:
            err = traceback.format_exc()
            retval = None
        else:
            err = None

        # tell the master we're done with that case
        comm.send((comm.rank, retval, err), 0, tag=2)


def concurrent_eval(func, cases, comm, allgather=False):
    """
    Runs the given function concurrently on all procs in the communicator.

    NOTE: This function should NOT be used if the concurrent function makes
    any internal collective MPI calls.

    Args
    ----

    func : function
        The function to execute in workers.

    cases : iter of function args
        Entries are assumed to be of the form (args, kwargs) where
        kwargs are allowed to be None and args should be a list or tuple.

    com : MPI communicator or None
        The MPI communicator that is shared between the master and workers.
        If None, the function will be executed serially.

    allgather : bool(False)
        If True, the results will be allgathered to all procs in the comm.
        Otherwise, results will be gathered to rank 0 only.
    """

    results = []

    if comm is None:
        it = cases
    else:
        it = islice(cases, comm.rank, None, comm.size)

    for args, kwargs in it:
        try:
            if kwargs:
                retval = func(*args, **kwargs)
            else:
                retval = func(*args)
        except:
            err = traceback.format_exc()
            retval = None
        else:
            err = None

        results.append((retval, err))

    if comm is not None:
        if allgather:
            allresults = comm.allgather(results)
            results = list(chain(*allresults))
        else:
            allresults = comm.gather(results, root=0)
            if comm.rank == 0:
                results = list(chain(*allresults))
            else:
                results = None

    return results