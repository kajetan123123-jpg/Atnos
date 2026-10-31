"""Register, but never start, one explicitly selected WB local Flux scene trial."""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('--scene', required=True)
    parser.add_argument('--seed', type=int, default=429512)
    parser.add_argument('--authorized', action='store_true')
    args = parser.parse_args()
    if not args.authorized:
        parser.error('Explicit --authorized is required; this command creates no receipt by default')
    from atnos.core.config import get_settings
    from atnos.production.wb_local_flux_trial import register_flux_trial
    result = register_flux_trial(get_settings(), args.job_id, args.scene,
                                 authorized=True, seed=args.seed)
    print(json.dumps({'registered': True, 'dispatch': False, 'job_id': args.job_id,
                     'scene_id': args.scene, 'digest': result['digest'], 'max_attempts': 1}, indent=2))


if __name__ == '__main__':
    main()
