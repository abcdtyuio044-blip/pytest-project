"""
A production-quality test filtering pipeline for pytest.

This module provides a flexible, extensible architecture for filtering pytest
test collections through a series of configurable stages.
"""

from abc import ABC, abstractmethod
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import pytest



class PipelineStep(ABC):
    """
    Abstract base class for all test filter steps.

    Each concrete step must implement the apply method to process a list
    of pytest.Item objects and return a filtered or transformed list.
    """
    
    @abstractmethod
    def apply(self, tests: List[pytest.Item]) -> Union[List[pytest.Item], Any]:
        """
        Apply filtering logic to the provided test items.
        
        Args:
            tests: List of pytest.Item objects to process
            
        Returns:
            Filtered list of pytest.Item objects or transformed data structure
        """
        pass

class TestFilterPipeline:
    """
    A pipeline that applies multiple test filter stages sequentially.
    
    Each stage receives the output of the previous stage, allowing for
    complex filtering workflows through composition.
    """
    
    def __init__(self, stages: Optional[List[PipelineStep]] = None) -> None:
        """
        Initialize the pipeline with an optional configuration and list of stages.
        
        Args:
            config: PipelineConfig instance for configuration-driven setup
            stages: List of PipelineStep instances to apply in order (overrides config)
        """
        self._stages: List[PipelineStep] = stages or []
        

    
    # def _build_stages_from_config(self) -> None:
    #     """Build pipeline stages based on configuration."""
    #     pipeline_order = self.config.get_pipeline_order()
        
    #     for stage_name in pipeline_order:
    #         if stage_name == 'date_filter':
    #             self._stages.append(DateFilterStage())
    #         elif stage_name == 'fixture_grouping':
    #             group_mappings = self.config.get_group_mappings()
    #             if group_mappings:
    #                 self._stages.append(FixtureParameterGroupingStage(
    #                     group_mappings=group_mappings,
    #                     config=self.config
    #                 ))
    
    def add_stage(self, stage: PipelineStep) -> "TestFilterPipeline":
        """
        Add a filter stage to the end of the pipeline.
        
        Args:
            stage: PipelineStep instance to add
            
        Returns:
            Self for method chaining
        """
        self._stages.append(stage)
        return self
    
    def apply(self, tests: List[pytest.Item]) -> Any:
        """
        Apply all stages in the pipeline sequentially.
        
        Args:
            tests: Initial list of pytest.Item objects
            
        Returns:
            Result after applying all stages (may be filtered tests or other data)
        """
        # Build stages from config if none provided
        if not self._stages:
            self._build_stages_from_config()
            
        result = tests
        for stage in self._stages:
            result = stage.apply(result)
        return result
    
    def clear(self) -> None:
        """Remove all stages from the pipeline."""
        self._stages.clear()
    
    @property
    def stages(self) -> List[PipelineStep]:
        """Get a copy of the current stages list."""
        return self._stages.copy()


class DateFilterStage(PipelineStep):
    """
    Filter stage that keeps tests based on day-of-week scheduling.
    
    Tests can be marked with @pytest.mark.run_days(...) to specify
    when they should run. Supports explicit weekdays and "weekend" keyword.
    """
    
    # Mapping of weekday names to datetime.weekday() values (Monday=0)
    WEEKDAY_MAP = {
        'sun': 0, 'sunday': 0,
        'mon': 1, 'monday': 1,
        'tue': 2, 'tuesday': 2,
        'wed': 3, 'wednesday': 3,
        'thu': 4, 'thursday': 4,
        'fri': 5, 'friday': 5,
        'sat': 6, 'saturday': 6
    }

    WEEKEND_DAYS = {5, 6}  # Friday, Saturday
    
    def __init__(self, current_time: Optional[datetime] = None) -> None:
        """
        Initialize the date filter stage.
        
        Args:
            current_time: Override current time for testing (defaults to now)
        """
        self._current_time = current_time
    
    def apply(self, tests: List[pytest.Item]) -> List[pytest.Item]:
        """
        Filter tests based on run_days marker and current day.
        
        Args:
            tests: List of pytest.Item objects to filter
            
        Returns:
            Filtered list containing only tests that should run today
        """
        current_day = (self._current_time or datetime.now()).weekday()
        filtered_tests = []
        
        for test in tests:
            if self._should_run_today(test, current_day):
                filtered_tests.append(test)
        
        return filtered_tests
    
    def _should_run_today(self, test: pytest.Item, current_day: int) -> bool:
        """
        Check if a test should run on the current day.
        
        Args:
            test: pytest.Item to check
            current_day: Current weekday (0=Monday, 6=Sunday)
            
        Returns:
            True if test should run today, False otherwise
        """
        run_days_mark = test.get_closest_marker('run_days')
        if not run_days_mark:
            # No marker means run always
            return True
        
        allowed_days = set()
        
        # Process marker arguments
        for arg in run_days_mark.args:
            if isinstance(arg, str):
                arg_lower = arg.lower()
                if arg_lower == 'weekend':
                    allowed_days.update(self.WEEKEND_DAYS)
                elif arg_lower in self.WEEKDAY_MAP:
                    allowed_days.add(self.WEEKDAY_MAP[arg_lower])
        
        # Process marker keyword arguments
        for key, value in getattr(run_days_mark, "kwargs", {}).items():
            if key.lower() == 'days' and isinstance(value, (list, tuple)):
                for day in value:
                    if isinstance(day, str):
                        day_lower = day.lower()
                        if day_lower == 'weekend':
                            allowed_days.update(self.WEEKEND_DAYS)
                        elif day_lower in self.WEEKDAY_MAP:
                            allowed_days.add(self.WEEKDAY_MAP[day_lower])
        
        return current_day in allowed_days if allowed_days else True


@dataclass
class FixtureParameterGroups:
    """Data structure holding grouped test results."""
    groups: Dict[str, List[pytest.Item]]
    unmatched: List[pytest.Item]
    
    def get_group(self, group_name: str) -> List[pytest.Item]:
        """Get tests for a specific group by name."""
        return self.groups.get(group_name, [])
    
    def get_all_groups(self) -> Dict[str, List[pytest.Item]]:
        """Get all groups as a dictionary."""
        return self.groups.copy()
    
    def get_group_names(self) -> List[str]:
        """Get list of all group names."""
        return list(self.groups.keys())


class FixtureParameterGroupingStage(PipelineStep):
    """
    Stage that groups tests based on fixture parameter values.
    Example:
        - Group "fast" includes tests which have fixture that has parameters containing "quick" or "mode_a"
        - Group "slow" includes tests which have fixture that has parameters containing "slow" or "mode_b"
    
    Inspects parametrized fixtures and groups tests based on parameter values
    containing configured identifier strings. Supports any number of named groups.

    Same test may belong to multiple groups, depending on its fixture parameter values !
    Example:
       test_foo(some_fixture): ...

       @pytest.mark.parametrize("some_fixture", ["quick_mode", "slow_detailed"])
       def test_foo(some_fixture):
           ....

        test_foo belongs to group "fast" when the fixture parameter contains "quick" 
        but to group "slow" when it contains "slow".
    """
    
    def __init__(
        self,
        group_mappings: Dict[str, List[str]],
    ) -> None:
        """
        Initialize the fixture parameter grouping stage.
        
        Args:
            fixture_name: Name of the fixture to inspect for parameters
            group_mappings: Dictionary mapping group names to lists of identifier strings
                          e.g., {"fast": ["quick", "mode_a"], "slow": ["slow", "detailed"]}
        """
        self.group_mappings = group_mappings
    
    def apply(self, tests: List[pytest.Item]):
        """
        Group tests based on fixture parameter values containing identifier strings.
        Adds dynamic markers to config file at the start of processing.
        
        Args:
            tests: List of pytest.Item objects to group
            
        Returns:
            FixtureParameterGroups containing the grouped tests with markers applied

        Example:
            Group "fast" includes tests which have fixture that has parameters containing "quick" or "mode_a"
            Group "slow" includes tests which have fixture that has parameters containing "slow" or "detailed"
        """
        
        groups = {group_name: [] for group_name in self.group_mappings.keys()}
        unmatched = []

        for test in tests:
            assigned_groups = self._find_matching_groups(test)
            print(f"Test {test.name} assigned to groups: {assigned_groups}")
            #TODO: Do we want test to be run on a single group only?
            if assigned_groups:
                # Add test to all matching groups
                for group_name in assigned_groups:
                    groups[group_name].append(test)
                    # Add marker to the test
                    test.add_marker(pytest.mark.__getattr__(group_name))
            else:
                unmatched.append(test)

        return tests
        # return FixtureParameterGroups(
        #     groups=groups,
        #     unmatched=unmatched
        # )

    def _find_matching_groups(self, test: pytest.Item) -> List[str]:
        """
        Find all groups that a test belongs to based on fixture parameter values.
        
        Args:
            test: pytest.Item to check
            
        Returns:
            List of group names the test belongs to
            
         Example:
            If a test contains a fixture that its value is "quick", it belongs to group "fast".
            If a test contains a fixture that its value is "slow", it belongs to group "slow".
        """
        matching_groups = []
        #take the params of the fixture the test uses and find matching groups
        parameter_values = []
        # Prefer callspec params (from @pytest.mark.parametrize or parametrized fixtures)
        callspec = getattr(test, "callspec", None)
        if callspec is not None:
            params = getattr(callspec, "params", {})
            if isinstance(params, dict):
                parameter_values.extend(params.values())
            else:
                try:
                    parameter_values.extend(list(params))
                except Exception:
                    pass

        # Fall back to funcargs (actual fixture values available at collection time)
        funcargs = getattr(test, "funcargs", None)
        if isinstance(funcargs, dict):
            parameter_values.extend(funcargs.values())

        # Ensure we always have a list to iterate
        if not parameter_values:
            parameter_values = []
        for param_value in parameter_values:
            for group_name, identifiers in self.group_mappings.items():
                if any(identifier.lower() in str(param_value).lower() for identifier in identifiers):
                    matching_groups.append(group_name)
        return matching_groups
    

# Example usage in pytest_collection_modifyitems hook
def pytest_collection_modifyitems(config: pytest.Config, items: List[pytest.Item]) -> None:
    """
    Example pytest hook showing how to use the test filter pipeline with configuration.
    
    This hook is called after collection is completed and allows modification
    of the collected test items based on pipeline.ini configuration.
    """
    print("Collected test items:")
    print(items)
        
    pipeline = TestFilterPipeline()
    
    #add only the FixtureParameterGroupingStage stage to the pipeline with {"fast": ["quick", "mode_a"], "slow": ["slow", "detailed"]}
    pipeline.add_stage(FixtureParameterGroupingStage({"fast": ["quick", "mode_a"], "slow": ["slow", "detailed"]}))

    # Apply the pipeline - stages are built from configuration
    result = pipeline.apply(items)
    items[:] = result
    for item in items:
        print(f"\nTEST: {item.nodeid}")
        print("MARKERS:", [m.name for m in item.iter_markers()])
    # # If the last stage was date filtering, update items with filtered list
    # if isinstance(result, list):
    #     items[:] = result
    
    # # If the last stage was grouping, handle the groups
    # elif isinstance(result, FixtureParameterGroups):
    #     # Example: run specific group based on command line option
    #     target_group = config.getoption("--group", default=None)
    #     if target_group and target_group in result.get_group_names():
    #         items[:] = result.get_group(target_group)
        
    #     # Example: exclude certain groups
    #     exclude_groups = config.getoption("--exclude-groups", default="").split(",")
    #     if exclude_groups and exclude_groups != [""]:
    #         filtered_items = []
    #         for test in items:
    #             # Check if test has any excluded group markers
    #             should_exclude = False
    #             for group in exclude_groups:
    #                 if test.get_closest_marker(group.strip()):
    #                     should_exclude = True
    #                     break
    #             if not should_exclude:
    #                 filtered_items.append(test)
    #         items[:] = filtered_items
    # print("????????????????????")
    # # for each test print the group it belongs to
    # for test in items:
    #     groups = test.get_closest_marker("group")
    #     if groups:
    #         print(f"Test {test.name} belongs to groups: {groups}")