export default {
    test: {
        globals: true,
        environment: 'node',
        // Only the frontend suite; keeps vitest out of gitignored copies such
        // as build/ and scratch/ that contain other projects' test files.
        include: ['tests/frontend/**/*.test.js'],
    },
};
