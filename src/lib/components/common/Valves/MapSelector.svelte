<script>
	export let setViewLocation = [51.505, -0.09];
	export let points = [];

	export let onClick = (e) => {};
</script>

<div class=" z-10 w-full">
	<div
		class="h-96 z-10 flex items-center justify-center rounded-lg bg-gray-100 text-sm text-gray-500 dark:bg-gray-800 dark:text-gray-400"
	>
		Map not available
	</div>
	<!-- props preserved for API compatibility: setViewLocation={setViewLocation}, points={points}, onClick={onClick} -->
</div>
